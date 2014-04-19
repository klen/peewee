import datetime
import unittest

from peewee import *
from playhouse.migrate import *

try:
    import psycopg2
    pg_db = PostgresqlDatabase('peewee_test')
except ImportError:
    pg_db = None

sqlite_db = SqliteDatabase(':memory:')

class Tag(Model):
    tag = CharField()

class Person(Model):
    first_name = CharField()
    last_name = CharField()
    dob = DateField(null=True)

MODELS = [
    Person,
    Tag,
]

class BaseMigrationTestCase(object):
    database = None
    migrator_class = None

    _person_data = [
        ('Charlie', 'Leifer', None),
        ('Huey', 'Kitty', datetime.date(2011, 5, 1)),
        ('Mickey', 'Dog', datetime.date(2008, 6, 1)),
    ]

    def setUp(self):
        for model_class in MODELS:
            model_class._meta.database = self.database
            model_class.drop_table(True)
            model_class.create_table()

        self.migrator = self.migrator_class(self.database)

    def test_add_column(self):
        # Create some fields with a variety of NULL / default values.
        df = DateTimeField(null=True)
        df_def = DateTimeField(default=datetime.datetime(2012, 1, 1))
        cf = CharField(max_length=200, default='')
        bf = BooleanField(default=True)
        ff = FloatField(default=0)

        # Create two rows in the Tag table to test the handling of adding
        # non-null fields.
        t1 = Tag.create(tag='t1')
        t2 = Tag.create(tag='t2')

        # Convenience function for generating `add_column` migrations.
        def add_column(field_name, field_obj):
            return self.migrator.add_column('tag', field_name, field_obj)

        # Run the migration.
        migrate(
            add_column('pub_date', df),
            add_column('modified_date', df_def),
            add_column('comment', cf),
            add_column('is_public', bf),
            add_column('popularity', ff))

        # Create a new tag model to represent the fields we added.
        class NewTag(Model):
            tag = CharField()
            pub_date = df
            modified_date = df_def
            comment = cf
            is_public = bf
            popularity = ff

            class Meta:
                database = self.database
                db_table = Tag._meta.db_table

        query = (NewTag
                 .select(
                     NewTag.id,
                     NewTag.tag,
                     NewTag.pub_date,
                     NewTag.modified_date,
                     NewTag.comment,
                     NewTag.is_public,
                     NewTag.popularity)
                 .order_by(NewTag.tag.asc()))

        # Verify the resulting rows are correct.
        self.assertEqual(list(query.tuples()), [
            (t1.id, 't1', None, datetime.datetime(2012, 1, 1), '', True, 0.0),
            (t2.id, 't2', None, datetime.datetime(2012, 1, 1), '', True, 0.0),
        ])

    def _create_people(self):
        for first, last, dob in self._person_data:
            Person.create(first_name=first, last_name=last, dob=dob)

    def get_column_names(self, tbl):
        cursor = self.database.execute_sql('select * from %s limit 1' % tbl)
        return set([col[0] for col in cursor.description])

    def test_drop_column(self):
        self._create_people()
        migrate(
            self.migrator.drop_column('person', 'last_name'),
            self.migrator.drop_column('person', 'dob'))

        column_names = self.get_column_names('person')
        self.assertEqual(column_names, set(['id', 'first_name']))

    def test_rename_column(self):
        self._create_people()
        migrate(
            self.migrator.rename_column('person', 'first_name', 'first'),
            self.migrator.rename_column('person', 'last_name', 'last'))

        column_names = self.get_column_names('person')
        self.assertEqual(column_names, set(['id', 'first', 'last', 'dob']))

        class NewPerson(Model):
            first = CharField()
            last = CharField()
            dob = DateField()

            class Meta:
                database = self.database
                db_table = Person._meta.db_table

        query = (NewPerson
                 .select(
                     NewPerson.first,
                     NewPerson.last,
                     NewPerson.dob)
                 .order_by(NewPerson.first))
        self.assertEqual(list(query.tuples()), self._person_data)

    def test_add_not_null(self):
        self._create_people()

        def addNotNull():
            with self.database.transaction():
                migrate(self.migrator.add_not_null('person', 'dob'))

        # We cannot make the `dob` field not null because there is currently
        # a null value there.
        self.assertRaises(IntegrityError, addNotNull)

        (Person
         .update(dob=datetime.date(2000, 1, 2))
         .where(Person.dob >> None)
         .execute())

        # Now we can make the column not null.
        addNotNull()

        # And attempting to insert a null value results in an integrity error.
        with self.database.transaction():
            self.assertRaises(
                IntegrityError,
                Person.create,
                first_name='Kirby',
                last_name='Snazebrauer')

    def test_drop_not_null(self):
        self._create_people()
        migrate(
            self.migrator.drop_not_null('person', 'first_name'),
            self.migrator.drop_not_null('person', 'last_name'))

        p = Person.create(first_name=None, last_name=None)
        query = (Person
                 .select()
                 .where(
                     (Person.first_name >> None) &
                     (Person.last_name >> None)))
        self.assertEqual(query.count(), 1)

    def test_rename_table(self):
        t1 = Tag.create(tag='t1')
        t2 = Tag.create(tag='t2')

        # Move the tag data into a new model/table.
        class Tag_asdf(Tag):
            pass
        self.assertEqual(Tag_asdf._meta.db_table, 'tag_asdf')

        # Drop the new table just to be safe.
        Tag_asdf.drop_table(True)

        # Rename the tag table.
        migrate(self.migrator.rename_table('tag', 'tag_asdf'))

        # Verify the data was moved.
        query = (Tag_asdf
                 .select()
                 .order_by(Tag_asdf.tag))
        self.assertEqual([t.tag for t in query], ['t1', 't2'])

        # Verify the old table is gone.
        with self.database.transaction():
            self.assertRaises(
                DatabaseError,
                Tag.create,
                tag='t3')

    def test_add_index(self):
        # Create a unique index on first and last names.
        columns = ('first_name', 'last_name')
        migrate(self.migrator.add_index('person', columns, True))

        Person.create(first_name='first', last_name='last')
        with self.database.transaction():
            self.assertRaises(
                IntegrityError,
                Person.create,
                first_name='first',
                last_name='last')

    def test_drop_index(self):
        # Create a unique index.
        self.test_add_index()

        # Now drop the unique index.
        migrate(self.migrator.drop_index(
            'person', 'person_first_name_last_name'))

        Person.create(first_name='first', last_name='last')
        query = (Person
                 .select()
                 .where(
                     (Person.first_name == 'first') &
                     (Person.last_name == 'last')))
        self.assertEqual(query.count(), 2)


class PostgresqlMigrationTestCase(BaseMigrationTestCase, unittest.TestCase):
    database = pg_db
    migrator_class = PostgresqlMigrator


class SqliteMigrationTestCase(BaseMigrationTestCase, unittest.TestCase):
    database = sqlite_db
    migrator_class = SqliteMigrator


    """
    def test_rename_column(self):
        t1 = Tag.create(tag='t1')

        with db.transaction():
            self.migrator.rename_column(Tag, 'tag', 'foo')

        curs = db.execute_sql('select foo from tag')
        rows = curs.fetchall()

        self.assertEqual(rows, [
            ('t1',),
        ])

    def test_drop_column(self):
        t1 = Tag.create(tag='t1')

        with db.transaction():
            self.migrator.drop_column(Tag, 'tag')

        curs = db.execute_sql('select * from tag')
        rows = curs.fetchall()

        self.assertEqual(rows, [
            (t1.id,),
        ])

    def test_set_nullable(self):
        t1 = Tag.create(tag='t1')

        with db.transaction():
            self.migrator.set_nullable(Tag, Tag.tag, True)

        t2 = Tag.create(tag=None)
        tags = [t.tag for t in Tag.select().order_by(Tag.id)]
        self.assertEqual(tags, ['t1', None])

        t2.delete_instance()

        with db.transaction():
            self.migrator.set_nullable(Tag, Tag.tag, False)

        with db.transaction():
            self.assertRaises(self.integrity_error, Tag.create, tag=None)

    def test_rename_table(self):
        t1 = Tag.create(tag='t1')

        self.migrator.rename_table(Tag, 'tagzz')
        curs = db.execute_sql('select * from tagzz')
        res = curs.fetchall()

        self.assertEqual(res, [
            (t1.id, 't1'),
        ])

        self.migrator.rename_table(Tag, 'tag')
    """
