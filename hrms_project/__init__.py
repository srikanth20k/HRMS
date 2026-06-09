# Use PyMySQL as a drop-in replacement for mysqlclient (MySQLdb).
# Pure-Python, so it installs without a C compiler on Windows/cPanel.
import pymysql

pymysql.install_as_MySQLdb()
