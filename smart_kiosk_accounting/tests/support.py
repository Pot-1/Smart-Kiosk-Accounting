import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import DEFAULT_BUSINESS_NAME
from database.models import Base, Business, Product, User
from database.seed_data import seed_demo_business
from database.tenant import TenantSession


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(
            bind=self.engine, class_=TenantSession, expire_on_commit=False
        )
        self.db = self.Session()
        self.db.info["system_operation"] = True
        with patch("database.seed_data.DEMO_MODE", True):
            seed_demo_business(self.db)
        self.db.commit()
        self.business = self.db.query(Business).filter_by(
            name=DEFAULT_BUSINESS_NAME
        ).one()
        self.product = self.db.query(Product).filter_by(
            business_id=self.business.id, sku="CHK-001"
        ).one()
        user = self.db.query(User).filter_by(username="admin").one()
        self.db.info.update(
            tenant_user_id=user.id,
            tenant_business_id=self.business.id,
        )
        self.db.info.pop("system_operation", None)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
