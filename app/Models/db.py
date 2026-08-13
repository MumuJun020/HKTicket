from dbutils.pooled_db import PooledDB
import pymysql
import pandas as pd
from ..app import app

with app.app_context():
    # 数据库连接配置
    config = {
        # 主机名
        'host': app.config['MYSQL_HOST'],
        # 数据库用户名
        'user': app.config['MYSQL_USER'],
        # 数据库密码
        'password': app.config['MYSQL_PASSWORD'],
        # 数据库 库名称
        'database': app.config['MYSQL_DATABASE'],
        # 数据库端口
        'port': app.config['MYSQL_PORT'],
        # 字符编码
        'charset': app.config['MYSQL_CHARSET'],
    }


#  释放连接对象
def connect_close(conn, cursor):
    cursor.close()
    conn.close()


class Mysql:
    def __init__(self):
        self.v = 1
        self.pool = PooledDB(
            creator=pymysql,  # 使用链接数据库的模块
            maxconnections=50,  # 连接池允许的最大连接数，0和None表示不限制连接数
            mincached=10,  # 初始化时，链接池中至少创建的空闲的连接，0表示不创建
            maxcached=5,  # 连接池中最多闲置的连接，0和None不限制

            # 链接池中最多共享的连接数量，0和None表示全部共享。
            # PS: 无用，因为pymysql和MySQLdb等模块的 threadsafety都为1，所有值无论设置为多少，_maxcached永远为0，所以永远是所有链接都共享。
            maxshared=0,

            blocking=True,  # 连接池中如果没有可用连接后，是否阻塞等待。True，等待；False，不等待然后报错
            maxusage=None,  # 一个链接最多被重复使用的次数，None表示无限制
            setsession=[],  # 开始会话前执行的命令列表。如：["set datestyle to ...", "set time zone ..."]

            # ping MySQL服务端，检查是否服务可用。 如：0 = None = never 1 = default = whenever it is requested, 2 = when a cursor is
            # created, 4 = when a query is executed, 7 = always
            ping=0,

            # 数据库连接配置
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            database=config['database'],
            charset=config['charset']
        )

    #  从连接池中获取一个连接对象
    def connect(self):
        conn = self.pool.connection()
        cursor = conn.cursor(cursor=pymysql.cursors.DictCursor)  # 以字典的方式显示
        return conn, cursor

    #  执行相应的sql语句，对数据库进行更新/插入/删除等操作
    def execute_db(self, sql):
        conn, cursor = self.connect()
        try:
            # 使用 execute() 执行sql
            cursor.execute(sql)
            # 提交事务，因为 pymysql 模块默认是启用事务的  sql语句如果不提交 相当于没有执行
            conn.commit()
            connect_close(conn, cursor)
        except Exception as e:
            connect_close(conn, cursor)
            print("操作出现错误：{}".format(e))
            # 回滚所有更改，所有操作都将不在数据库中执行
            conn.rollback()

    #  多行数据插入
    def execute_many(self, sql, list_tuple):
        """
        当需要在表中插入多行数据的时候，使用该函数，不仅效力高，而且简便，目的性强
        sql语句的形式为：insert into table(attribute1，attribute2，......) values(%s,%s,......)
        list_tuple是一个列表，列表中的每一个元素必须是元组！！！元组元素为要插入的值，注意属性与值的对应
        eg:
           list_tuple = [('001','张三',18), ('002','李四',19), ('003','王五',20)]
            sql = "insert into student(SNO，Sname，Sage) values(%s,%s,%s)"
            executemany_db(sql, list_tuple)
        """
        conn, cursor = self.connect()
        try:
            # 使用 executemany() 执行sql
            cursor.executemany(sql, list_tuple)
            # 提交事务
            conn.commit()
            connect_close(conn, cursor)
        except Exception as e:
            connect_close(conn, cursor)
            print("操作出现错误：{}".format(e))
            # 回滚所有更改，所有操作都将不在数据库中执行
            conn.rollback()

    #  将DataFrame对象存入连接好的数据库
    def insert_DataFrame(self, df, table_name, dtypedict=None, index=False):
        """
        :param df: DataFrame数据
        :param table_name: 表名称
        :param dtypedict: 存入一个字典对象，设置表中字段的数据类型，默认为None
        :param index: 是否将索引写入数据库，默认为False
        """
        conn, cursor = self.connect()
        try:
            if dtypedict is None:
                df.to_sql(name=table_name, con=conn, if_exists="replace", index=index)
            else:
                df.to_sql(name=table_name, con=conn, if_exists="replace", index=index, dtypedict=dtypedict)
            connect_close(conn, cursor)
        except Exception as e:
            connect_close(conn, cursor)
            print("操作出现错误：{}".format(e))
            return None

    #  执行用于查询的sql语句，返回所有的查询结果
    def select_db(self, sql):
        """
        eg:
            [{"SNO":'001', "Sname":'张三', "Sage":18},
            {"SNO":'002', "Sname":'李四', "Sage":19},
            {"SNO":'003', "Sname":'王五', "Sage":20}]
        eg:
            mysql = module.MySql('root', '*****', 'DB', datatype='dict')
            sql = 'select * from table'
            mysql.select_db(sql)
        """
        try:
            conn, cursor = self.connect()
            # 使用 execute() 执行sql
            cursor.execute(sql)
            # 使用 fetchall() 获取查询结果
            data = cursor.fetchall()
            connect_close(conn, cursor)
            return data
        except Exception as e:
            print("操作出现错误：{}".format(e))
            return None

    #  执行用于查询的sql语句，查询的结果以DataFrame对象形式返回
    def select_db_to_DateFrame(self, sql):
        conn, cursor = self.connect()
        try:
            df = pd.read_sql(sql, con=conn)  # 获取数据
            connect_close(conn, cursor)
            return df
        except Exception as e:
            connect_close(conn, cursor)
            print("操作出现错误：{}".format(e))
            return None


if __name__ == '__main__':
    pass
