import psycopg2
import itertools
from contextlib import contextmanager

psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)


class Database(object):

    def __init__(self, host, port, database, user, password):
        self.connection = psycopg2.connect(host=host,
                                           port=port,
                                           database=database,
                                           user=user,
                                           password=password)

    def row(self, query, **params):
        cursor = self._cursor_for_query(query, params)
        row = cursor.fetchone()
        cursor.close()
        return row

    def scalar(self, query, **params):
        row = self.row(query, **params)
        return row[0]

    def rows(self, query, **params):
        cursor = self._cursor_for_query(query, params)
        for row in cursor:
            yield row
        cursor.close()

    def _cursor_for_query(self, query, params):
        cursor = self.connection.cursor()
        cursor.execute(query, params)
        return cursor

    @contextmanager
    def transaction(self):
        try:
            yield
            self.connection.commit()
        except:
            self.connection.rollback()
            raise


class Build(object):

    def __init__(self, project, database):
        self.database = database
        self.project = project
        self._id = None
        self._version = None

    @property
    def id(self):
        if not self._id:
            self.refresh()
        return self._id

    @property
    def version(self):
        if not self._version:
            self.refresh()
        return self._version

    def refresh(self):
        query = """
        SELECT
            builds.id,
            builds.name
        FROM
            builds
            INNER JOIN testplans
                ON testplans.id = builds.testplan_id
                INNER JOIN testprojects
                    ON testprojects.id = testplans.testproject_id
        WHERE
            testprojects.notes = %(project)s
        ORDER BY
            builds.creation_ts DESC
        LIMIT 1
        """

        row = self.database.row(query, project=self.project)
        self._id = row[0]
        self._version = row[1]


db = None
build = None

CTE = {
    'latest_executions': """
        latest_executions AS
        (
            SELECT
                executions.tcversion_id AS tcversion_id,
                MAX(executions.execution_ts) AS execution_ts
            FROM
                executions
            GROUP BY
                executions.tcversion_id
        )
    """,
    'latest_executed': """
        latest_executed AS
        (
            SELECT
                executions.tester_id            AS tester_id,
                MAX(executions.execution_ts)    AS execution_ts
            FROM
                executions
            GROUP BY
                executions.tester_id
        )
    """,
    'path_tree': """
        RECURSIVE path_tree(id, name) AS
        (
            SELECT
                parent.id,
                CAST(parent.name as varchar(200)) as name
            FROM
                nodes_hierarchy parent
            WHERE
                parent.parent_id = (
                    SELECT testplans.testproject_id
                    FROM builds
                    INNER JOIN testplans ON builds.testplan_id = testplans.id
                    WHERE builds.id = %(build_id)s
                )
            UNION ALL
            SELECT
                child.id,
                CAST(path_tree.name || '/' || child.name as varchar(200)) as name
            FROM
                path_tree
                INNER JOIN nodes_hierarchy child
                    ON path_tree.id = child.parent_id
                    AND child.node_type_id = 2
        )
    """
}


def cte(*tables):
    query = "WITH\n"
    query += ",\n".join(CTE[t] for t in tables)
    return query


def setup(**kwargs):
    global db
    db = Database(kwargs['host'],
                  kwargs['port'],
                  kwargs['database'],
                  kwargs['user'],
                  kwargs['password'])

    global build
    build = Build(kwargs['project'],
                  db)


def total_manual_tests():
    query = """
    SELECT
        count(tcversions.id)
    FROM
        tcversions
        INNER JOIN testplan_tcversions
            ON tcversions.id = testplan_tcversions.tcversion_id
            INNER JOIN builds
                ON builds.testplan_id = testplan_tcversions.testplan_id
    WHERE
        tcversions.execution_type = 1
        AND builds.id = %(build_id)s
    GROUP BY
        builds.id
    """

    return db.scalar(query, build_id=build.id)


def test_statuses():
    query = cte('latest_executions') + """
    SELECT
        (CASE executions.status
         WHEN 'p' THEN 'passed'
         WHEN 'f' THEN 'failed'
         WHEN 'b' THEN 'blocked'
         ELSE executions.status
         END)                       AS status,
        COUNT(executions.status)    AS total
    FROM
        executions
    INNER JOIN latest_executions
        ON executions.tcversion_id = latest_executions.tcversion_id
        AND executions.execution_ts = latest_executions.execution_ts
    INNER JOIN builds
        ON builds.id = executions.build_id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
    GROUP BY
        executions.status
    """

    statuses = {
        'passed': 0,
        'failed': 0,
        'blocked': 0,
    }

    rows = db.rows(query, build_id=build.id)
    statuses.update(dict((key, value) for key, value in rows))

    return statuses


def tests_for_status(status):
    query = cte('latest_executions') + """
    SELECT
        tcversions.tc_external_id       AS number,
        parent.name                     AS name,
        executions.notes                AS notes
    FROM
        executions
        INNER JOIN latest_executions
            ON executions.tcversion_id = latest_executions.tcversion_id
            AND executions.execution_ts = latest_executions.execution_ts
        INNER JOIN tcversions
            ON executions.tcversion_id = tcversions.id
            INNER JOIN nodes_hierarchy node
                ON tcversions.id = node.id
                INNER JOIN nodes_hierarchy parent
                    ON node.parent_id = parent.id
        INNER JOIN builds
            ON builds.id = executions.build_id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
        AND executions.status = %(status)s
    ORDER BY
        tcversions.tc_external_id
    """

    rows = db.rows(query, build_id=build.id, status=status)

    tests = [
        {'name': "X-%s: %s" % (row[0], row[1]),
         'notes': row[2].strip()}
        for row in rows]

    return tests


def failed_tests():
    return tests_for_status('f')


def blocked_tests():
    return tests_for_status('b')


def executed_per_person():
    query = cte('latest_executions') + """
    SELECT
        users.first || ' ' || users.last    AS name,
        (CASE executions.status
         WHEN 'p' THEN 'passed'
         WHEN 'f' THEN 'failed'
         WHEN 'b' THEN 'blocked'
         ELSE executions.status
         END)                               AS status,
        COUNT(executions.id)                AS executed
    FROM
        executions
        INNER JOIN latest_executions
            ON executions.tcversion_id = latest_executions.tcversion_id
            AND executions.execution_ts = latest_executions.execution_ts
        INNER JOIN builds
            ON builds.id = executions.build_id
        INNER JOIN users
            ON executions.tester_id = users.id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
    GROUP BY
        (users.first || ' ' || users.last),
        executions.status
    """

    rows = db.rows(query, build_id=build.id)

    scores = {}
    for row in rows:
        name, status, executed = row
        scores.setdefault(name, {})[status] = executed

    result = [{'name': key, 'executed': value}
              for key, value in scores.iteritems()]

    result.sort(reverse=True, key=lambda x: sum(x['executed'].values()))

    return result


def path_for_test(tcversion_id):
    query = """
    WITH RECURSIVE test_path(name, id, parent_id) AS
    (
        SELECT name, id, parent_id FROM nodes_hierarchy WHERE id = (
            SELECT
                parent.parent_id
            FROM
                nodes_hierarchy node
                INNER JOIN nodes_hierarchy parent
                    ON node.parent_id = parent.id
            WHERE
                node.id = %(tcversion_id)s
        )
        UNION ALL
            SELECT
                child.name,
                child.id,
                child.parent_id
            FROM
                test_path
                INNER JOIN nodes_hierarchy child
                    ON test_path.parent_id = child.id
                    AND child.node_type_id = 2
    )
    SELECT name FROM test_path
    """

    rows = db.rows(query, tcversion_id=tcversion_id)
    names = [row[0] for row in rows]

    return " / ".join(reversed(names))


def path_per_person():
    query = cte('latest_executed') + """
    SELECT
        users.first || ' ' || users.last    AS name,
        executions.tcversion_id             AS tcversion_id
    FROM
        executions
        INNER JOIN latest_executed
            ON executions.tester_id = latest_executed.tester_id
            AND executions.execution_ts = latest_executed.execution_ts
        INNER JOIN builds
            ON builds.id = executions.build_id
        INNER JOIN users
            ON executions.tester_id = users.id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
    GROUP BY
        (users.first || ' ' || users.last),
        executions.tcversion_id
    """

    rows = db.rows(query, build_id=build.id)
    results = dict((row[0], path_for_test(row[1])) for row in rows)

    return results


def build_testers():
    scores = executed_per_person()
    paths = path_per_person()

    for person in scores:
        person['last_path'] = paths[person['name']]

    return scores


def dashboard():
    with db.transaction():
        failed = failed_tests()
        blocked = blocked_tests()
        testers = build_testers()

        stats = test_statuses()
        stats['total'] = total_manual_tests()

    return {
        'version': build.version,
        'stats': stats,
        'failed': failed,
        'blocked': blocked,
        'testers': testers
    }


def manual_test_report():
    query = cte('path_tree', 'latest_executions') + """
    SELECT
        path_tree.name                      AS folder,
        tcversions.tc_external_id           AS number,
        parent.name                         AS name,
        tcversions.version                  AS version,
        (CASE executions.status
        WHEN 'p' THEN 'passed'
        WHEN 'f' THEN 'failed'
        WHEN 'b' THEN 'blocked'
        ELSE executions.status
        END)                                AS status
    FROM
        executions
        INNER JOIN latest_executions
            ON executions.tcversion_id = latest_executions.tcversion_id
            AND executions.execution_ts = latest_executions.execution_ts
        INNER JOIN builds
            ON builds.id = executions.build_id
        INNER JOIN tcversions
            ON executions.tcversion_id = tcversions.id
            INNER JOIN nodes_hierarchy node
                ON tcversions.id = node.id
                INNER JOIN nodes_hierarchy parent
                    ON node.parent_id = parent.id
                    LEFT OUTER JOIN path_tree
                        ON parent.parent_id = path_tree.id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
    ORDER BY
        path_tree.name ASC,
        parent.node_order DESC
    """

    with db.transaction():
        rows = db.rows(query, build_id=build.id)
        tests = group_executions_by_folder(rows)

    return {'version': build.version,
            'tests': tests}


def all_logs(timestamp=None):
    query = cte('path_tree', 'latest_executions') + """
    SELECT
        path_tree.name                      AS folder,
        tcversions.tc_external_id           AS number,
        parent.name                         AS name,
        (CASE executions.status
        WHEN 'p' THEN 'passed'
        WHEN 'f' THEN 'failed'
        WHEN 'b' THEN 'blocked'
        ELSE executions.status
        END)                                AS status,
        executions.execution_ts             AS timestamp,
        users.first                         AS firstname,
        users.last                          AS lastname,
        users.first || ' ' || users.last    AS user
    FROM
        executions
        INNER JOIN latest_executions
            ON executions.tcversion_id = latest_executions.tcversion_id
            AND executions.execution_ts = latest_executions.execution_ts
        INNER JOIN users
            ON executions.tester_id = users.id
        INNER JOIN builds
            ON builds.id = executions.build_id
        INNER JOIN tcversions
            ON executions.tcversion_id = tcversions.id
            INNER JOIN nodes_hierarchy node
                ON tcversions.id = node.id
                INNER JOIN nodes_hierarchy parent
                    ON node.parent_id = parent.id
                    LEFT OUTER JOIN path_tree
                        ON parent.parent_id = path_tree.id
    WHERE
        builds.id = %(build_id)s
        AND executions.execution_type = 1
    """

    if timestamp:
        query += "AND executions.execution_ts >= %(timestamp)s"

    query += """
    ORDER BY
        executions.execution_ts ASC
    """

    with db.transaction():
        rows = db.rows(query, build_id=build.id, timestamp=timestamp)
        return tuple({'folder': row[0],
                      'number': row[1],
                      'name': row[2],
                      'status': row[3],
                      'timestamp': row[4],
                      'firstname': row[5],
                      'lastname': row[6],
                      'user': row[7]} for row in rows)


def group_executions_by_folder(rows):
    report = []
    key = lambda row: row[0]

    for folder, rows in itertools.groupby(rows, key):
        executions = tuple({'number': row[1],
                            'name': row[2],
                            'version': row[3],
                            'status': row[4]} for row in rows)
        report.append((folder, executions))

    return report
