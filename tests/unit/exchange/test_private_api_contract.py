from app.exchange.okx.private_api import OkxPrivateApiClient
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient


def test_demo_private_rest_client_satisfies_private_api_contract() -> None:
    assert issubclass(OkxDemoPrivateRestClient, OkxPrivateApiClient)
