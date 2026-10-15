"""main.py 的离线契约测试。

这些测试不联网、也不需要真实 API Key：用 monkeypatch 把
``httpx.AsyncClient.post`` 换成返回构造好的假响应，从而验证 main.py
对外表现出的契约（输入校验、转发内容、取答路径、错误行为）。

其中两个测试刻意断言了当前实现的已知缺陷（见各自的 docstring）。
这样做的好处是：一旦缺陷被修复，测试会失败并提醒同步更新，
而不是留一个永远为真的空断言。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import main


class FakeResponse:
    """冒充 httpx.Response，只提供 main.py 真正用到的成员。"""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("this fake response carries no JSON body")
        return self._payload


def stub_upstream(monkeypatch, response):
    """把 httpx.AsyncClient.post 替换成直接返回给定响应。

    返回替换后的函数，测试可以从它身上读到最后一次调用的 url 和参数，
    以此检查转发层到底发出去了什么。
    """

    async def fake_post(self, url, **kwargs):
        fake_post.last_url = url
        fake_post.last_kwargs = kwargs
        return response

    fake_post.last_url = None
    fake_post.last_kwargs = None

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return fake_post


def ok_response(content="你好，我是测试回答。"):
    """构造一个结构正常的 DeepSeek 成功响应。"""
    return FakeResponse(
        200,
        payload={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


# --------------------------------------------------------------- (a) 接口层


def test_chat_route_is_registered():
    """``POST /chat`` 出现在 OpenAPI 文档里。"""
    client = TestClient(main.app)

    paths = client.get("/openapi.json").json()["paths"]

    assert "post" in paths["/chat"]


def test_ok_returns_reply_field(monkeypatch):
    """上游正常时，响应体是 ``{"reply": <content>}``。"""
    stub_upstream(monkeypatch, ok_response("回答文本"))
    client = TestClient(main.app)

    resp = client.post("/chat", json={"message": "你好"})

    assert resp.status_code == 200
    assert resp.json() == {"reply": "回答文本"}


@pytest.mark.parametrize(
    "body",
    [
        {},                 # 缺 message 字段
        {"msg": "你好"},     # 字段名写错
        {"message": 123},   # 类型写错
    ],
)
def test_invalid_body_is_rejected_before_upstream(monkeypatch, body):
    """非法请求在进入转发逻辑之前就被拦下（422），不会打到上游。"""
    fake = stub_upstream(monkeypatch, ok_response())
    client = TestClient(main.app)

    resp = client.post("/chat", json=body)

    assert resp.status_code == 422
    assert fake.last_url is None, "校验失败的请求不应触发任何上游调用"


# --------------------------------------------------------------- (b) 转发层


def test_forwards_to_deepseek_with_bearer_header(monkeypatch):
    """请求发到 /chat/completions，并带上 Bearer 凭证与正确的 payload。"""
    monkeypatch.setattr(main, "API_KEY", "sk-test-123")
    fake = stub_upstream(monkeypatch, ok_response())
    client = TestClient(main.app)

    client.post("/chat", json={"message": "讲个笑话"})

    assert fake.last_url == "https://api.deepseek.com/chat/completions"
    assert fake.last_kwargs["headers"]["Authorization"] == "Bearer sk-test-123"
    assert fake.last_kwargs["json"] == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "讲个笑话"}],
    }


# --------------------------------------------------------------- (c) 取答层


def test_extracts_answer_from_choices_path(monkeypatch):
    """答案取自 choices[0].message.content，而不是别的字段。"""
    stub_upstream(monkeypatch, ok_response("取自 content 的回答"))
    client = TestClient(main.app)

    resp = client.post("/chat", json={"message": "你好"})

    assert resp.json()["reply"] == "取自 content 的回答"


def test_upstream_error_is_reported_inside_reply(monkeypatch):
    """上游非 200：错误被写进 reply，但 HTTP 状态码仍然是 200。

    这是当前实现的真实行为，同时也是它的一个缺陷——调用方无法靠状态码
    区分成功与失败。若日后改成抛 HTTPException 返回 502，本测试会失败，
    届时应同步更新。
    """
    stub_upstream(monkeypatch, FakeResponse(401, text='{"error":"invalid api key"}'))
    client = TestClient(main.app)

    resp = client.post("/chat", json={"message": "你好"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == '上游错误 401: {"error":"invalid api key"}'


def test_upstream_200_with_unexpected_shape_raises(monkeypatch):
    """上游返回 200 但缺少 choices：当前实现没有兜底，会抛 KeyError。

    同样是刻意记录的已知缺陷。
    """
    stub_upstream(monkeypatch, FakeResponse(200, payload={"unexpected": True}))
    client = TestClient(main.app)

    with pytest.raises(KeyError):
        client.post("/chat", json={"message": "你好"})
