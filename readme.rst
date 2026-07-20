================
Incremental Serv
================

这是 `incremental.icu <https://github.com/inrenping/incremental.icu>`前端对应的后端接口。通过 Github Actions 自动部署。

快速开始
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


