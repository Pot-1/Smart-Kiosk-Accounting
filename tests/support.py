import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import DEFAULT_BUSINESS_NAME
from database.models import Base, Business, Product
from database.seed_data import seed_demo_business


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        seed_demo_business(self.db)
        self.db.commit()
        self.business = self.db.query(Business).filter_by(
            name=DEFAULT_BUSINESS_NAME
        ).one()
        self.product = self.db.query(Product).filter_by(
            business_id=self.business.id, sku="CHK-001"
        ).one()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
