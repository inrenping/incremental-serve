================
Incremental Serv
================

这是 `incremental.icu <https://github.com/inrenping/incremental.icu>`前端对应的后端接口。通过 Github Actions 自动部署。

注意：服务器的内存需大于 1GB，否则安装相关依赖不足。

`快速部署`
========

注意

> garth 的可用版本不支持 python 3.14 以上的版本，如果您使用的新版本的 python，请用 uv 管理使用 3.12 版本运行。

安装服务
--------

.. code-block:: bash

    apt update    

    python3 -m venv venv
    source venv/bin/activate

    pip install -r requirements.txt
    pip install gunicorn
    # 如果是用 uv 管理
    uv pip install -r requirements.txt
    uv pip install gunicorn

*记得配置好环境变量*

启动服务
--------

.. code-block:: bash

    # uvicorn app.main:app --reload
    python -m uvicorn app.main:app
    # 如果是用 uv 管理
    uv run uvicorn app.main:app --reload

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

在服务器上生成 SSH 密钥对：

.. code-block:: bash

    ssh-keygen -t ed25519 -f ~/.ssh/github_actions_deploy -C "github-actions"

把公钥添加到服务器域名：

.. code-block:: bash

ssh-copy-id -i ~/.ssh/github_actions_deploy.pub root@你的服务器IP

在服务器上 git clone 本项目，并按照直接运行的方式先跑一遍确定可以运行：

.. code-block:: bash

    git clone <repository-url> /var/www/incremental-serve
    cd /var/www/incremental-serve
    # 手动配置 .env 文件。
    # 安装依赖
    pip install -r requirements.txt
    # 启动服务
    uvicorn app.main:app --host 0.0.0.0 --port 8000

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

OAuth 授权流程
==============

本项目内置了两套 Token 体系，分别服务于普通用户登录和 OAuth 第三方接入（如 ChatGPT GPT Actions）。

Token 有效期一览
----------------

普通用户登录（JWT）：

+--------------------------+------------+----------------------+
| Token 类型               | 有效期     | 说明                 |
+--------------------------+------------+----------------------+
| Access Token             | 60 分钟    | 调用 API 的凭证      |
| Refresh Token            | 7 天       | 用于续期 Access Token|
+--------------------------+------------+----------------------+

OAuth 授权码流程（第三方应用接入）：

+--------------------------+------------+----------------------+
| 凭证类型                 | 有效期     | 说明                 |
+--------------------------+------------+----------------------+
| 授权码 (Auth Code)       | 10 分钟    | 一次性，用完即失效   |
| Access Token             | 365 天     | 调用 API 的凭证      |
| Refresh Token            | 400 天     | 用于续期 Access Token|
+--------------------------+------------+----------------------+

其他：

+--------------------------+------------+
| 类型                     | 有效期     |
+--------------------------+------------+
| 邮箱验证码               | 5 分钟     |
+--------------------------+------------+

OAuth 授权流程详解
------------------

OAuth 授权码流程遵循 RFC 6749 规范，整个过程自动完成，用户只需在浏览器中确认授权：

1. **第三方应用发起授权**
   用户在第三方应用（如 OpenAI）点击授权，浏览器跳转到 ``/oauth/authorize?client_id=...&redirect_uri=...``

2. **用户登录验证**
   服务端渲染登录页面（内嵌 HTML），用户输入邮箱和验证码完成身份验证。
   验证码通过 ``/api/v1/auth/send-captcha`` 发送。

3. **用户确认授权**
   登录成功后显示授权确认页面，用户选择「允许」或「拒绝」。

4. **生成授权码并重定向**
   用户点击「允许」后，服务端生成一个 **10 分钟有效** 的授权码，重定向回第三方应用的 ``redirect_uri``，同时携带 ``code`` 参数。

5. **第三方应用换取 Token**
   第三方服务器用授权码调用 ``POST /oauth/token``，换到 **365 天** 的 Access Token 和 **400 天** 的 Refresh Token。

6. **后续 API 调用**
   第三方应用拿着 Access Token（Bearer 方式）访问你的 API。Access Token 过期后用 Refresh Token 续期。

为什么授权码只有 10 分钟？
   授权码通过浏览器 URL 参数传递，暴露风险高。它只是临时的「兑换券」，第三方服务器通常几秒内就会用它换到长期 Token。10 分钟是 OAuth 2.0 规范推荐的安全实践。

相关端点
--------

+---------------------------+---------+--------------------------------------+
| 端点                      | 方法    | 说明                                 |
+---------------------------+---------+--------------------------------------+
| /oauth/authorize          | GET     | OAuth 授权登录页面                   |
+---------------------------+---------+--------------------------------------+
| /oauth/authorize          | POST    | 提交登录表单，渲染授权确认页面       |
+---------------------------+---------+--------------------------------------+
| /oauth/consent            | POST    | 处理用户允许/拒绝，生成授权码并重定向|
+---------------------------+---------+--------------------------------------+
| /oauth/token              | POST    | 授权码换取 Access Token + Refresh Token |
+---------------------------+---------+--------------------------------------+


