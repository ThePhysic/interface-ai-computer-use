from interface_cu.demo_app import create_app


def test_search_detail_savings_and_errors():
    client = create_app().test_client()
    assert b'<iframe name="workspace"' in client.get("/").data
    assert b"Open member" in client.get("/members?member_id=06789").data
    assert b"Morgan Sample" in client.get("/members/06789").data
    assert b"USD 9,876.54" in client.get("/members/06789/savings").data
    assert b"No such member" in client.get("/members?member_id=99999").data
    assert b"five-digit" in client.get("/members?member_id=bad").data
    assert client.post("/transfer").status_code == 403
