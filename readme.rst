Blunt Serv
==========

这是一个基于 FastAPI 的用户认证服务项目。

快速开始
--------

启动服务器
~~~~~~~~~~

.. code-block:: python

    uvicorn app.main:app --reload

更新 requirements.txt
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    pip freeze > requirements.txt

TODO
----

以下是项目的待办事项列表：

* ✓ 已登录账号自动登录
* ✓ 通过邮件验证码登录
* ✓ 通过 Google 登录
* ✓ 通过 GitHub 登录
* ✓ 通过邮件验证码注册
* ✓ 通过 Google 注册
* ✓ 通过 GitHub 注册
* 修改邮箱
* 删除关联绑定
* 注销账号
* 用户信息展示
* 用户操作日志展示
* 用户其他信息完善和修改。

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