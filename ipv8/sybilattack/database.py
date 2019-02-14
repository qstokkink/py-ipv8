from ..database import Database


class TraceDB(Database):

    def insert_measurement(self, address, sybil_count, type, measurement_list):
        inserted_id = self.execute(
            u"INSERT INTO identifiers (ip, port, sybil_count, type) VALUES(?,?,?,?)",
            (address[0], address[1], sybil_count, type), get_lastrowid=True)
        for measurement in measurement_list:
            self.execute(
                u"INSERT INTO measurements (id, ip, time) VALUES(?,?,?)",
                (inserted_id, measurement[0], measurement[1]))
        self.commit()

    def table_create_script(self):
        return u"""
        CREATE TABLE IF NOT EXISTS identifiers(
         id                   INTEGER PRIMARY KEY,
         ip                   TEXT NOT NULL,
         port                 INTEGER NOT NULL,
         sybil_count          INTEGER NOT NULL,
         type        	      TEXT NOT NULL
         );

        CREATE TABLE IF NOT EXISTS measurements(
         measurement          INTEGER PRIMARY KEY,
         id                   INTEGER NOT NULL,
         ip                   TEXT NOT NULL,
         time        	      DECIMAL NOT NULL,

         FOREIGN KEY(id) REFERENCES identifiers(id)
         );
         """

    def check_database(self, database_version):
        self.executescript(self.table_create_script())
        self.commit()
        return 0