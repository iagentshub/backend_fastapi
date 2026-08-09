"""Los endpoints JSON publican requestBody tipado en OpenAPI."""


def _schema_name(operation):
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    ref = body_schema.get("$ref")
    if ref:
        return ref.rsplit("/", 1)[-1]
    refs = [item.get("$ref") for item in body_schema.get("anyOf", [])]
    return next(ref.rsplit("/", 1)[-1] for ref in refs if ref)


def test_auth_and_billing_request_bodies_are_typed(client):
    schema = client.app.openapi()
    register = schema["paths"]["/api/auth/register"]["post"]["requestBody"]
    quote = schema["paths"]["/api/billing/quote"]["post"]["requestBody"]
    register_ref = register["content"]["application/json"]["schema"]["$ref"]
    quote_ref = quote["content"]["application/json"]["schema"]["$ref"]
    assert register_ref.endswith("/RegisterBody")
    assert quote_ref.endswith("/PlanBody")


def test_remaining_json_request_bodies_are_typed(client):
    paths = client.app.openapi()["paths"]
    expected = {
        ("/api/accounts", "post"): "AccountBody",
        ("/api/accounts/test", "post"): "AccountBody",
        ("/api/accounts/github/device-token", "post"): "DeviceCodeBody",
        ("/api/accounts/{account_id}", "put"): "AccountBody",
        ("/api/accounts/{account_id}/sync", "post"): "AccountSyncBody",
        ("/api/admin/agents/{agent_id}", "put"): "ResourcePayload",
        ("/api/admin/groups/{group_id}/status", "post"): "StatusBody",
        ("/api/admin/users/{username}", "patch"): "AdminUserPatchBody",
        ("/api/admin/users", "post"): "AdminUserCreateBody",
        ("/api/agents", "post"): "AgentPayload",
        ("/api/agents/{agent_id}/chat", "post"): "AgentChatBody",
        ("/api/auth/github/device-token", "post"): "DeviceCodeBody",
        ("/api/auth/tokens", "post"): "PatCreateBody",
        ("/api/auth/vscode/authorize", "post"): "VSCodeAuthorizeBody",
        ("/api/auth/vscode/exchange", "post"): "VSCodeExchangeBody",
        ("/api/chats/{agent_id}", "post"): "ConversationBody",
        ("/api/connections/ollama-models", "post"): "OllamaModelsBody",
        ("/api/connections/test-all", "post"): "ConnectionTestsBody",
        ("/api/connections", "post"): "ConnectionPayload",
        ("/api/groups/{group_id}/status", "post"): "StatusBody",
        ("/api/groups/{group_id}/transfer-ownership", "post"): "UsernameBody",
        ("/api/knowledge/text", "post"): "KnowledgeTextBody",
        ("/api/knowledge/url", "post"): "KnowledgeUrlBody",
        ("/api/memory/{filename}", "post"): "MemoryBody",
        ("/api/prompts/{scope}", "post"): "CatalogResourcePayload",
        ("/api/skills/{scope}", "post"): "CatalogResourcePayload",
        ("/api/tools/{scope}", "post"): "CatalogResourcePayload",
    }
    for (path, method), model in expected.items():
        assert _schema_name(paths[path][method]) == model


def test_typed_body_ignores_unknown_legacy_fields():
    from app.models.request_bodies import AccountBody

    body = AccountBody.model_validate(
        {"provider": "openai", "legacy_frontend_field": "ignored"}
    )
    assert body.payload() == {"provider": "openai"}
