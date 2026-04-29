Blunt Serv
==========

这是一个基于 FastAPI 的后端接口。

快速开始
--------

安装服务
~~~~~~~~~~

.. code-block:: bash

    apt update
    apt install python3-pip python3-venv -y

    python3 -m venv venv
    source venv/bin/activate

    pip install -r requirements.txt

    pip install gunicorn

记得配置好环境变量

启动服务
~~~~~~~~~~

.. code-block:: bash

    # uvicorn app.main:app --reload
    python -m uvicorn app.main:app --reload

更新 requirements.txt
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    pip freeze > requirements.txt

在服务器上查看日志
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    sudo journalctl -u incremental-serve.service -f

TODO
----

以下是项目的待办事项列表：

* ✓ 已登录账号自动登录
* ✓ 通过邮件验证码登录
* ✓ 通过 Google 登录
* ✓ 通过 GitHub 登录
* ✓ 通过邮件验证码注册
* ✓ 通过 Google 注册*
* ✓ 通过 GitHub 注册
* 修改用户名，修改邮箱（先做显示暂不做修改）
* 查看关联绑定，支持重新绑定
* 注销账号(后续再做)
* ✓ 用户信息展示
* 用户操作日志展示（后续再做）
* 用户其他信息完善和修改（还没加字段）

* 获取佳明运动数据，同步到数据库
* 上传运动数据到佳明（单条上传）

* 获取佳明心率数据，同步到数据库（后续再做）
* 获取佳明睡眠数据，同步到数据库（后续再做）
* 运动数据，网页上 Table 格式展示（后续再做）
* 心率数据，网页上 Table 格式展示（后续再做）
* 睡眠数据，网页上 Table 格式展示（后续再做）



部署说明
--------

直接运行
~~~~~~~~

1. 下载代码，进入目录

   .. code-block:: bash

       git clone <repository-url> /path/to/directory

   手动配置 .env 文件。

2. 创建并激活虚拟环境：

   .. code-block:: bash

       python3 -m venv venv
       source venv/bin/activate

3. 安装依赖

   .. code-block:: bash

       pip install --upgrade pip
       pip install -r requirements.txt

4. 直接运行

   .. code-block:: bash

       uvicorn app.main:app --host 0.0.0.0 --port 8000

使用 Systemd 运行
~~~~~~~~~~~~~~~~~~

创建服务文件：

.. code-block:: ini

    sudo vi /etc/systemd/system/incremental-serve.service

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
~~~~~~~~

.. code-block:: bash

    sudo systemctl daemon-reload
    sudo systemctl start incremental-serve.service
    sudo systemctl status incremental-serve.service

Github Actions 部署
~~~~~~~~~~~~~~~~~~

需要在 secrets 中添加以下环境变量：

.. code-block:: bash

    REMOTE_HOST=服务器IP
    REMOTE_USER=服务器用户名
    SSH_PRIVATE_KEY=远程登录用到
    DATABASE_URL=postgresql://user:password@localhost:5432/db_name
    # SCHEMA=your_schema_name
    SECRET_KEY=your_secret_key_here
    RESEND_API_KEY=re_your_secret_key_here
    RESEND_EMAIL_FROM=Your App <noreply@yourdomain.com>
    GOOGLE_CLIENT_ID=你的_CLIENT_ID
    GOOGLE_CLIENT_SECRET=你的_CLIENT_SECRET
    # GITHUB 不允许 GITHUB_ 开头的环境变量，所以改成 GIT_HUB_ 开头
    GIT_HUB_CLIENT_ID=你的_CLIENT_ID
    GIT_HUB_CLIENT_SECRET=你的_CLIENT_SECRET


佳明相关 API
~~~~~~~~~~~~~~~~~~

.. code-block:: json

GARMIN_URL_DICT = {
  "garmin_connect_activities" : "/activitylist-service/activities/search/activities",
  "garmin_connect_fit_download": "/download-service/files/activity",
  "garmin_connect_upload": "/upload-service/upload"
}


[Garmin API SDK](https://github.com/cyberjunky/python-garminconnect.git)


上传接口 

https://connectapi.garmin.com/upload-service/upload/