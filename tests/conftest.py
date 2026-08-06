import os

# Set before test modules import app globals. Unit/API tests must not reuse
# production async connection pools across TestClient event loops.
os.environ["ENVIRONMENT"] = "test"
