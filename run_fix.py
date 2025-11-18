from pathlib import Path

from fix_1 import main as fix_1
from fix_2 import main as fix_2
from fix_3 import main as fix_3

db_path = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw/config/chroniken.sqlite3")
if __name__ == '__main__':
    fix_2(db_path)
    fix_3(db_path)
    fix_1(db_path)
