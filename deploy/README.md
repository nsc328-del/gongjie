# 服务器部署

线上地址：https://111.229.128.77/gongjie/

Python 应用在 `/opt/gongjie/app`，虚拟环境在 `/opt/gongjie/venv`，由 `gongjie.service` 管理，监听 `127.0.0.1:8768`。Nginx HTTPS 站点引入 `/etc/nginx/snippets/gongjie.conf`，HTTP 对应路径跳转到 HTTPS。页面与 API 由应用访问密码保护，每个密码对应一个独立存档空间，`/gongjie/login` 输入密码后签发有效期 30 天的 Secure、HttpOnly、SameSite Cookie。各空间密码哈希、私有数据目录和会话签名密钥保存在 `/opt/gongjie/app/data/access.json`，由 `GONGJIE_ACCESS_FILE` 指定；不要求账号。

仅上传 `gongjie/`、`static/`、`requirements.txt` 与 `worldbooks/白昼将死/白昼将死.worldbook.json`。不要同步本地 `data/`、其他世界书或 iOS 目录。原用户数据库和模型密钥位于应用下的 `data/`；试玩空间位于 `data/spaces/test/`。更新代码时全部保留。访问配置使用 `spaces` 字典，每项含 `salt`、`password_hash`、`data_dir`、`label`；Cookie 签名包含空间标识。不得从请求参数直接选择数据目录。每个空间各自保存 DeepSeek V4 Flash 配置，初始 Key 相同但可独立修改，密钥不进入源码或部署配置。

更新依赖后用 `sudo systemctl restart gongjie` 重启应用；修改代理配置后先执行 `sudo nginx -t` 再 reload。流式响应要求关闭代理缓冲。单进程运行以保持世界操作锁一致。请求的空间身份通过 ContextVar 贯穿完整流式响应；没有认证身份不能访问任何服务器存档。模型设置中的“退出并切换存档”会清除 Cookie 并重新载入密码页。

验证 `/gongjie/` 未登录跳转密码页，`/gongjie/api/bootstrap` 未登录返回 401，登录后页面、静态资源与 API 正常。证书由服务器现有的 IP 证书续期机制管理。
