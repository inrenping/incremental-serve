================
Incremental Serv
================

这是 `incremental.icu <https://github.com/inrenping/incremental.icu>`_ 的后端接口，代码基本通过 Google Gemini Assist 生成。

快速开始
========

安装服务
--------

.. code-block:: bash

    apt update    

    python3 -m venv venv
    source venv/bin/activate

    pip install -r requirements.txt
    pip install gunicorn

*记得配置好环境变量*

启动服务
--------

.. code-block:: bash

    # uvicorn app.main:app --reload
    python -m uvicorn app.main:app --reload

更新 requirements.txt
---------------------

.. code-block:: bash

    pip freeze > requirements.txt

在服务器上查看日志
------------------

.. code-block:: bash

    sudo journalctl -u incremental-serve.service -f

部署说明
========

直接运行
--------

1. 下载代码，进入目录：

   .. code-block:: bash

       git clone <repository-url> /path/to/directory

   手动配置 ``.env`` 文件。

2. 创建并激活虚拟环境：

   .. code-block:: bash

       python3 -m venv venv
       source venv/bin/activate

3. 安装依赖：

   .. code-block:: bash

       pip install --upgrade pip
       pip install -r requirements.txt

4. 直接运行：

   .. code-block:: bash

       uvicorn app.main:app --host 0.0.0.0 --port 8000

使用 Systemd 运行
-----------------

创建服务文件：

.. code-block:: bash

    sudo vi /etc/systemd/system/incremental-serve.service

在文件中写入以下配置：

.. code-block:: ini

    [Unit]
    Description=incremental-serve deploy
    # 确保在网络就绪后再启动
    After=network.target

    [Service]
    # 运行服务的用户
    User=root
    # 程序所在的目录
    WorkingDirectory=/var/www/incremental-serve
    Environment="PYTHONPATH=/var/www/incremental-serve"
    # 启动命令（必须使用绝对路径）
    ExecStart=/var/www/incremental-serve/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
    # 如果程序崩溃，5秒后自动重启
    Restart=always
    RestartSec=5

    [Install]
    # 定义服务在系统运行级别下的启动方式
    WantedBy=multi-user.target

启动测试
--------

.. code-block:: bash

    sudo systemctl daemon-reload
    sudo systemctl start incremental-serve.service
    sudo systemctl status incremental-serve.service


MCP (Model Context Protocol) 接口
====================================

本项目实现了 MCP 服务，支持 AI 客户端（如 Claude Desktop、Cursor、Trae IDE 等）
通过标准协议查询用户的运动数据。目前提供 **两个接入通道**，共享同一套业务逻辑。

可用工具
--------

目前提供 3 个 MCP 工具：

- ``get_activities`` — 查询运动活动明细（距离、时长、心率、配速等），支持按运动类型过滤和分页
- ``get_activity_stats`` — 查询运动统计汇总（按天/周/月分组聚合）
- ``get_heart_rate_data`` — 查询每日心率数据（每日汇总 + 每分钟采样明细）

源码位于 ``app/mcp/tools/`` 目录下。

通道一：原生 MCP（stdio / SSE）
--------------------------------

适用于 Claude Desktop、Cursor、Trae IDE 等原生 MCP 客户端。

**stdio 模式**（本地开发/IDE 集成）:

.. code-block:: bash

    python -m app.mcp.server

**SSE 模式**（远程服务，默认启用 JWT Bearer 认证）:

.. code-block:: bash

    python -m app.mcp.server --transport sse --port 8001

    # 如需关闭认证（仅限内网）:
    python -m app.mcp.server --transport sse --port 8001 --no-auth

通道二：OpenAI Function Calling（HTTP API）
--------------------------------------------

适用于任何通过 HTTP 调用的 LLM 应用。认证方式为 JWT Bearer Token
（需先通过 OAuth 登录获取 token）。

**获取工具定义**:

.. code-block:: bash

    GET /api/v1/openai/tools
    Authorization: Bearer <jwt_token>

**执行工具调用**:

.. code-block:: bash

    POST /api/v1/openai/execute
    Authorization: Bearer <jwt_token>
    Content-Type: application/json

    {
        "function_name": "get_activity_stats",
        "arguments": {
            "start_date": "2026-06-01",
            "end_date": "2026-07-13",
            "group_by": "month"
        }
    }

调用时无需传入 ``user_id``，系统会自动从 JWT Token 中提取当前认证用户身份。

OpenAI GPT Actions 配置示例
-----------------------------

在 ChatGPT 的 GPT Actions 配置中，将 ``openai/tools`` 和 ``openai/execute`` 端点配置为自定义 Action：

.. code-block:: yaml

    openapi: "3.1.0"
    info:
      title: "Incremental Fitness API"
      description: "查询用户的运动活动和心率数据"
      version: "1.0.0"
    servers:
      - url: "https://incremental.icu/api/v1"
    paths:
      /openai/tools:
        get:
          operationId: "getTools"
          description: "获取可用的工具列表"
          security:
            - bearerAuth: []
          responses:
            "200":
              description: "工具定义列表"
      /openai/execute:
        post:
          operationId: "executeTool"
          description: "执行指定的工具函数"
          security:
            - bearerAuth: []
          requestBody:
            required: true
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    function_name:
                      type: string
                      description: "工具名称：get_activities / get_activity_stats / get_heart_rate_data"
                    arguments:
                      type: object
                      description: "工具参数（无需传 user_id，会自动注入）"
          responses:
            "200":
              description: "工具执行结果"
    components:
      securitySchemes:
        bearerAuth:
          type: http
          scheme: bearer

然后在 GPT Actions 的 **Authentication** 中选择 ``Bearer`` 类型，
填入你的 JWT Token（通过 OAuth 登录获取，见下方认证说明）。

