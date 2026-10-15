# chatbot

一个最小的 FastAPI 后端：`POST /chat` 收到 `message` 后转发给 DeepSeek 的 `/chat/completions`，把模型回答以 `{"reply": "..."}` 返回。仓库里附带一个 `test.html`，浏览器里点一下就能试。

```
main.py     后端（接口层 + 转发层 + 取答层）
test.html   浏览器测试页，POST 到 http://127.0.0.1:8000/chat
.gitignore  内容只有 .env
```

---

## 第一部分 · 启动说明

### 1. 安装依赖

```bash
pip install fastapi uvicorn httpx python-dotenv
```

| 包 | 作用 |
|---|---|
| `fastapi` | Web 框架，负责路由、请求体解析与校验 |
| `uvicorn` | ASGI 服务器，真正把 `app` 跑起来 |
| `httpx` | 异步 HTTP 客户端，用它在后端转发请求给 DeepSeek |
| `python-dotenv` | 从 `.env` 读取 API Key |

### 2. 配置 API Key

在项目根目录新建 `.env`：

```
API_KEY=你的Key
```

> ⚠️ **变量名必须是 `API_KEY`。**
> 代码里读的是 `os.getenv("API_KEY")`（`main.py` 第 10 行）。如果写成 `DEEPSEEK_API_KEY`，这里取到的就是 `None`，请求头会变成 `Authorization: Bearer None`，上游直接返回 401。
>
> 如果你更想用 `DEEPSEEK_API_KEY` 这个名字，把 `main.py` 第 10 行改成 `API_KEY = os.getenv("DEEPSEEK_API_KEY")` 即可，两边保持一致就行。

**`.env` 不能提交。** 仓库里的 `.gitignore` 已经处理好了（文件内容就是 `.env`，4 字节），所以 `git status` 里看不到 `.env` 是正常的。可以用这两条命令自查：

```bash
git check-ignore -v .env     # 有输出 => 已被忽略
git status --short           # 不应出现 .env
```

### 3. 启动

```bash
uvicorn main:app --reload
```

服务监听 **`http://127.0.0.1:8000`**（uvicorn 默认端口）。`test.html` 里写死的地址也是 `127.0.0.1:8000`，端口必须对得上才能用。

启动成功时终端会打印：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

配置缺了 Key 也不会启动失败，但每次请求都会被上游拒绝（401），而客户端拿到的仍然是一个 HTTP 200 —— 具体见第三部分。

### 4. 真实请求 / 返回示例

下面这条请求是实际跑通过的一次，返回也是原样贴出的：

**请求**

```bash
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"message\":\"你好，请用一句话介绍你自己\"}"
```

等价地，浏览器里打开 `test.html`，在输入框里填 `你好，请用一句话介绍你自己` 然后点「发送」，页面会把返回的 JSON 显示出来。

**返回**（HTTP 状态码 `200`）

```json
{"reply":"你好！我是DeepSeek，一个由深度求索公司创造的AI助手，随时准备用热情和专业知识帮你解答问题、提供建议，让我们的交流既高效又愉快！"}
```

请求体只有一个字段 `message`；返回体也只有一个字段 `reply`，里面是模型的纯文本回答。

---

## 第二部分 · 三部分逻辑自述

### ① 接口层：收 `message`，返回 `{reply: ...}`

`main.py` 第 13–16 行：

```python
class ChatIn(BaseModel):
    message:str
@app.post("/chat")
async def chat(req:ChatIn):
```

- `ChatIn` 继承 `pydantic.BaseModel`，声明请求体必须是一个带 `message` 字段的 JSON 对象。`message:str` 没有默认值，所以它是必填项。
- 客户端传 `{"message":"你好"}`，FastAPI 会把它解析并校验成一个 `ChatIn` 实例，绑定到 `req` 上，所以函数体里可以直接用 `req.message`。
- 传成 `{"msg":"你好"}`（字段名错）或 `{"message":123}`（类型错），请求根本进不到 `chat()`，FastAPI 会直接返回 422，不会消耗上游额度。
- `req:ChatIn` 这个类型标注就是「接线」：FastAPI 看到参数是 `BaseModel` 子类，才把它当成**请求体**；如果写成 `message:str`，那就会被当成 URL 查询参数（`/chat?message=...`），接口形态完全变了。
- `@app.post("/chat")` 只负责声明「POST 方法 + `/chat` 路径」，不参与数据校验。

返回在第 29 行：

```python
    return {"reply": answer}
```

直接返回一个 dict，FastAPI 自动序列化成 JSON。这就是对外契约的**返回**部分：任何情况下都只有一个 `reply` 字段。

### ② 转发层：用 `httpx` 带 `Authorization: Bearer` 发到 `/chat/completions`

第 17–23 行：

```python
    payload={"model":MODEL,"messages": [{"role": "user", "content": req.message}]}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
```

**拼请求体**

- `payload` 有两个字段：`model` 用常量 `MODEL`（`"deepseek-chat"`），`messages` 是消息数组。
- `messages` 是**数组**，因为大模型接口是无状态的：服务器不记得上一轮说了什么，要多轮对话就得每次把完整历史重发一遍。这里只放了一条，所以效果是单轮问答。
- `{"role": "user", "content": req.message}` 就是接口层收进来的那句话，`role` 标成 `user` 表示「这是用户说的」。

**发请求**

- `httpx.AsyncClient()` 是异步客户端；用 `async with` 包起来，请求处理完会自动关闭连接。
- URL 由 `BASE_URL`（`"https://api.deepseek.com"`）拼上 `/chat/completions` 得到。
- `headers` 里的 `Authorization: Bearer {API_KEY}` 是**身份凭证**：访问的是第三方服务器，双方之间没有登录态和 Cookie，每一次请求都必须自带这把钥匙，DeepSeek 靠它判断扣谁的额度。注意 `Bearer` 和 Key 之间**有一个空格**，少一个就是 401。
- `headers` 里的 `Content-Type: application/json` 告诉对方「我发的是 JSON」。这一项其实是冗余的——用 `json=payload` 时 `httpx` 会自动序列化并设好这个头——但写上无害。
- `json=payload` 把上面的 dict 作为 JSON 请求体发出。
- `await` 表示等网络往返完成；返回值 `r` 是响应对象，后面的 `r.status_code`、`r.text`、`r.json()` 都是在读它。

### ③ 取答层：从 `r.json()["choices"][0]["message"]["content"]` 取回答，以及非 200 的处理

**正常路径**（第 26–29 行）：

```python
    print(r.status_code)
    print(r.json())
    answer = r.json()["choices"][0]["message"]["content"]
    return {"reply": answer}
```

DeepSeek 的返回是一个大 JSON，结构大致是：

```json
{
  "choices": [
    { "message": { "role": "assistant", "content": "回答文本在这里" } }
  ],
  "usage": { "...": "..." }
}
```

所以取值路径是一层层往下钻，每一层都有存在的理由：

| 片段 | 拿到什么 | 为什么有这一层 |
|---|---|---|
| `r.json()` | dict | 整个响应体，除了答案还有 `id`、`usage`、`model` 等元信息 |
| `["choices"]` | list | 答案放在候选列表里，因为接口支持一次返回多个结果（请求参数 `n`），默认只有 1 个 |
| `[0]` | dict | 取第一个候选 |
| `["message"]` | dict | 候选里除了消息还有 `finish_reason`（结束原因）等字段 |
| `["content"]` | str | 消息里除了 `content` 还有 `role` 等字段，纯文本才在这里 |

前两行 `print` 只是开发期调试，把状态码和原始 JSON 打到**服务端**控制台，不会返回给客户端。

**非 200 的处理**（第 24–25 行）：

```python
        if r.status_code != 200:
            return {"reply": f"上游错误 {r.status_code}: {r.text}"}
```

代码**确实做了非 200 处理**：上游不是 200 时不让流程走到下面那行硬取值，避免 `KeyError` 直接崩成 500。

但有三点要如实说明，这是我实测出来的真实行为：

1. **它返回给客户端的仍是 HTTP 200。** 上游出错的信息被塞进 `reply` 字符串里，而不是用一个错误状态码。实测用无效 Key 请求时，客户端收到的是：
   ```json
   {"reply":"上游错误 401: {\"error\":{\"message\":\"Authentication Fails, Your api key: ****test is invalid (request_id: 934077be-1e63-4ad5-821a-799eddd6d0e5)\",\"type\":\"authentication_error\",\"param\":null,\"code\":\"invalid_request_error\"}}"}
   ```
   与此同时 uvicorn 的访问日志记录的是 `"POST /chat HTTP/1.1" 200 OK`。
   也就是说调用方**没法靠状态码区分成功和失败**，只能去检查 `reply` 是不是以「上游错误」开头。这是个明显的设计缺陷，正常该用 `HTTPException` 返回 502/504。

2. **这段代码在 `async with` 块内部 `return`**，所以走上错误分支时，下面两行 `print` 和取答逻辑都不会执行——服务端控制台不会打印上游的状态码和响应体，排查时看不到细节。

3. **只判断了 `status_code != 200`，没有处理异常。** 网络不通、DNS 失败、连接超时等情况会从 `await client.post(...)` 直接抛 `httpx` 异常，没人接，最终变成 500。

另外，正常路径上的取值 `r.json()["choices"][0]["message"]["content"]` 仍然没有兜底：如果上游返回 200 但结构不符合预期（缺 `choices` 或 `content`），这里会抛 `KeyError` / `IndexError`。

---

## 已知取舍

- `allow_origins=["*"]` 允许任何来源跨域访问，只适合本地自测，上线前应改成具体域名。
- 每个请求都新建一个 `httpx.AsyncClient`，高并发下会反复建连接；更好的做法是在应用启动时建一个共享客户端。
- 接口只接受单条 `message`，不支持多轮上下文。
- 没有超时设置，上游不响应时会一直挂着。
