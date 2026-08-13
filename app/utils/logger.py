"""
日志管理器：用于捕获和存储运行时的日志输出，供前端实时显示
"""
import sys
import threading
from typing import Dict, List, Set
from datetime import datetime
from threading import Lock
import uuid
import queue
import json


class LogManager:
    """线程安全的日志管理器，支持SSE推送"""
    
    def __init__(self):
        self._logs: Dict[str, List[Dict[str, str]]] = {}
        self._lock = Lock()
        self._max_logs_per_task = 1000  # 每个任务最多保存的日志条数
        # SSE支持：为每个任务维护一个队列列表，每个SSE客户端对应一个队列
        self._sse_queues: Dict[str, List[queue.Queue]] = {}  # task_id -> List[Queue]
        # 全局日志队列（用于task_id为None的情况，接收所有任务的日志）
        self._global_queues: List[queue.Queue] = []  # 全局队列列表
        self._sse_lock = Lock()
    
    def create_task(self, task_id: str = None) -> str:
        """创建新的日志任务，返回任务ID"""
        if task_id is None:
            task_id = str(uuid.uuid4())
        with self._lock:
            if task_id not in self._logs:
                self._logs[task_id] = []
        with self._sse_lock:
            if task_id not in self._sse_queues:
                self._sse_queues[task_id] = []
        return task_id
    
    def register_sse_client(self, task_id: str = None) -> queue.Queue:
        """注册SSE客户端，返回一个队列用于接收日志
        如果task_id为None，则注册为全局客户端，接收所有任务的日志
        """
        q = queue.Queue(maxsize=100)  # 限制队列大小，避免内存溢出
        with self._sse_lock:
            if task_id is None:
                # 全局客户端
                self._global_queues.append(q)
            else:
                # 特定任务的客户端
                if task_id not in self._sse_queues:
                    self._sse_queues[task_id] = []
                self._sse_queues[task_id].append(q)
        return q
    
    def unregister_sse_client(self, task_id: str = None, q: queue.Queue = None):
        """注销SSE客户端"""
        with self._sse_lock:
            if task_id is None:
                # 全局客户端
                try:
                    self._global_queues.remove(q)
                except ValueError:
                    pass
            else:
                # 特定任务的客户端
                if task_id in self._sse_queues:
                    try:
                        self._sse_queues[task_id].remove(q)
                    except ValueError:
                        pass
    
    def add_log(self, task_id: str, message: str, level: str = "info"):
        """添加日志到指定任务，并推送到所有SSE客户端"""
        log_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": str(message)
        }
        
        with self._lock:
            if task_id not in self._logs:
                self._logs[task_id] = []
            
            self._logs[task_id].append(log_entry)
            
            # 限制日志数量，保留最新的
            if len(self._logs[task_id]) > self._max_logs_per_task:
                self._logs[task_id] = self._logs[task_id][-self._max_logs_per_task:]
        
        # 推送到所有SSE客户端
        with self._sse_lock:
            # 检查是否有任务特定的客户端连接
            has_task_specific_clients = task_id in self._sse_queues and len(self._sse_queues[task_id]) > 0
            
            # 添加到任务特定的队列
            if has_task_specific_clients:
                # 创建队列的副本，避免在遍历时修改原列表
                queues_to_notify = list(self._sse_queues[task_id])
                for q in queues_to_notify:
                    try:
                        # 非阻塞方式添加，如果队列满了则跳过
                        q.put_nowait(log_entry)
                    except queue.Full:
                        # 队列满了，尝试移除最旧的日志并添加新的
                        try:
                            q.get_nowait()  # 移除最旧的
                            q.put_nowait(log_entry)  # 添加新的
                        except queue.Empty:
                            pass
            
            # 添加到全局队列（只有当该任务没有特定客户端时才推送，避免重复）
            # 如果任务有特定客户端，只推送到特定客户端；否则推送到全局队列
            if not has_task_specific_clients:
                global_log_entry = {
                    **log_entry,
                    "task_id": task_id  # 添加task_id信息
                }
                global_queues_copy = list(self._global_queues)
                for q in global_queues_copy:
                    try:
                        q.put_nowait(global_log_entry)
                    except queue.Full:
                        try:
                            q.get_nowait()
                            q.put_nowait(global_log_entry)
                        except queue.Empty:
                            pass
    
    def get_logs(self, task_id: str, since_index: int = 0) -> Dict:
        """获取指定任务的日志（从指定索引开始）"""
        with self._lock:
            if task_id not in self._logs:
                return {"logs": [], "total": 0, "new_count": 0}
            
            all_logs = self._logs[task_id]
            new_logs = all_logs[since_index:]
            
            return {
                "logs": new_logs,
                "total": len(all_logs),
                "new_count": len(new_logs)
            }
    
    def clear_task(self, task_id: str):
        """清除指定任务的日志和SSE队列"""
        with self._lock:
            if task_id in self._logs:
                del self._logs[task_id]
        with self._sse_lock:
            if task_id in self._sse_queues:
                # 发送结束信号给所有队列
                for q in self._sse_queues[task_id]:
                    try:
                        q.put_nowait(None)  # None作为结束信号
                    except queue.Full:
                        pass
                del self._sse_queues[task_id]
    
    def cleanup_old_tasks(self, keep_recent: int = 10):
        """清理旧任务，只保留最近 N 个任务"""
        with self._lock:
            if len(self._logs) > keep_recent:
                # 按任务创建时间排序（简单实现：按任务ID字典序）
                task_ids = sorted(self._logs.keys())
                for task_id in task_ids[:-keep_recent]:
                    del self._logs[task_id]


# 全局日志管理器实例
log_manager = LogManager()


class TaskStateManager:
    """任务状态管理器：管理任务的运行、暂停、停止状态"""
    
    def __init__(self):
        self._states: Dict[str, str] = {}  # task_id -> state
        self._lock = Lock()
    
    def create_task(self, task_id: str):
        """创建任务并设置为运行状态"""
        with self._lock:
            self._states[task_id] = "running"
    
    def pause_task(self, task_id: str) -> bool:
        """暂停任务，返回是否成功"""
        with self._lock:
            if task_id in self._states and self._states[task_id] == "running":
                self._states[task_id] = "paused"
                return True
            return False
    
    def resume_task(self, task_id: str) -> bool:
        """恢复任务，返回是否成功"""
        with self._lock:
            if task_id in self._states and self._states[task_id] == "paused":
                self._states[task_id] = "running"
                return True
            return False
    
    def stop_task(self, task_id: str) -> bool:
        """停止任务，返回是否成功"""
        with self._lock:
            if task_id in self._states:
                self._states[task_id] = "stopped"
                return True
            return False
    
    def get_state(self, task_id: str) -> str:
        """获取任务状态，如果任务不存在返回None"""
        with self._lock:
            return self._states.get(task_id)
    
    def remove_task(self, task_id: str):
        """移除任务状态"""
        with self._lock:
            if task_id in self._states:
                del self._states[task_id]
    
    def wait_for_resume(self, task_id: str) -> bool:
        """等待任务恢复（在暂停状态下）。如果任务被停止，返回False"""
        while True:
            state = self.get_state(task_id)
            if state == "running":
                return True
            elif state == "stopped":
                return False
            elif state == "paused":
                import time
                time.sleep(0.5)  # 等待0.5秒后再次检查
            else:
                return False


# 全局任务状态管理器实例
task_state_manager = TaskStateManager()


class LogCapture:
    """捕获 print 输出的上下文管理器"""
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.original_stdout = None  # 在__enter__中设置
        self.original_stderr = None  # 在__enter__中设置
        self.buffer = ""  # 用于缓冲不完整的消息
        self._thread_id = threading.get_ident()  # 记录线程ID
    
    def write(self, message: str):
        """重定向写入到日志管理器"""
        if not isinstance(message, str):
            message = str(message)
        
        # 先输出到原标准输出（确保控制台能看到）
        if self.original_stdout:
            self.original_stdout.write(message)
            self.original_stdout.flush()
        
        # 将消息添加到缓冲区
        self.buffer += message
        
        # 如果缓冲区包含换行符，则处理完整的行
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            if line.strip():  # 只记录非空行
                try:
                    log_manager.add_log(self.task_id, line.strip(), "info")
                except Exception as e:
                    # 如果日志记录失败，至少输出到控制台
                    if self.original_stdout:
                        self.original_stdout.write(f"[日志记录失败: {e}]\n")
                        self.original_stdout.flush()
    
    def flush(self):
        """刷新输出"""
        # 如果有剩余的缓冲区内容，也记录它
        if self.buffer.strip():
            try:
                log_manager.add_log(self.task_id, self.buffer.strip(), "info")
            except Exception as e:
                if self.original_stdout:
                    self.original_stdout.write(f"[日志记录失败: {e}]\n")
                    self.original_stdout.flush()
            self.buffer = ""
        if self.original_stdout:
            self.original_stdout.flush()
    
    def writable(self):
        """返回True，表示流是可写的"""
        return True
    
    def __enter__(self):
        # 确保任务已创建
        log_manager.create_task(self.task_id)
        # 保存当前线程的stdout和stderr
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        # 重定向stdout和stderr
        sys.stdout = self
        sys.stderr = self
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 退出时刷新缓冲区，确保所有日志都被记录
        try:
            self.flush()
        except Exception:
            pass
        # 恢复原来的stdout和stderr
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        return False

