import pytest

from accounts.models import User

PASSWORD = "Correct-Horse-42"


@pytest.fixture
def make_user(db):
    def _make(email="user@bfsuk.org", full_name="Sam Ordering", status=User.Status.APPROVED,
              role=User.Role.ORDERING, password=PASSWORD):
        return User.objects.create_user(email, full_name, password, status=status, role=role)
    return _make


@pytest.fixture
def admin_user(make_user):
    return make_user("admin@bfsuk.org", "Alex Admin", role=User.Role.ADMIN)


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client
