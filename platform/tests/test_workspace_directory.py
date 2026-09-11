import pytest

from workspace.directory import CognitoDirectory


class DirectoryClient:
    def __init__(self):
        self.calls = []

    def get_paginator(self, operation):
        assert operation == "list_users"
        return self

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"Users": [
            {"Enabled": True, "Attributes": [{"Name": "sub", "Value": "one"}, {"Name": "name", "Value": "기획 담당"}]},
            {"Enabled": False, "Attributes": [{"Name": "sub", "Value": "disabled"}]},
        ]}, {"Users": [
            {"Enabled": True, "Attributes": [{"Name": "sub", "Value": "one"}]},
            {"Enabled": True, "Attributes": [{"Name": "sub", "Value": "two"}, {"Name": "email", "Value": "designer@example.test"}]},
        ]}]


def test_owner_lookup_uses_bounded_pagination_and_only_public_directory_fields():
    client = DirectoryClient()
    lookup = CognitoDirectory("pool", client)
    result = lookup("designer@")
    assert result == [{"sub": "one", "displayName": "기획 담당"}, {"sub": "two", "displayName": "designer@example.test"}]
    assert client.calls[0]["Filter"] == 'email ^= "designer@"'
    assert client.calls[0]["PaginationConfig"] == {"MaxItems": 40, "PageSize": 20}
    lookup("11111111-2222-3333-4444-555555555555")
    assert client.calls[-1]["Filter"].startswith("sub = ")


def test_empty_search_never_lists_the_entire_user_pool():
    client = DirectoryClient()
    lookup = CognitoDirectory("pool", client)
    for query in ["", " ", "a", "line\nbreak"]:
        with pytest.raises(ValueError):
            lookup(query)
    assert client.calls == []
