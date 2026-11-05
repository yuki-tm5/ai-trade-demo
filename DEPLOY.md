# Demo V0.1 云部署交付标准

必须提供实际可分享 HTTPS URL，并实测查询至原件下载；localhost 不算最终交付。当前目录是唯一允许发布的源码目录，禁止上传上级项目或历史验收包。数据仅来自明确虚构的T08补充样本；复制后的数据库已移除开发机路径和原有模拟数据集。

Streamlit Community Cloud：将本目录内容提交到单独GitHub仓库，登录 https://share.streamlit.io 创建应用，选择该仓库和 app.py，Python 3.12，部署后保存实际URL。无需Secrets或Ollama。更新时只提交本目录审核后的变更，Cloud重新部署后再测。

本地检查：python -m streamlit run app.py。下载按钮提供经SHA-256验证的原始文件字节。所有运行文件通过项目相对路径访问，不依赖Windows或原机器开机。

未完成：GitHub/Streamlit账号发布、线上URL和真实网页操作核验。不能将本地AppTest当作线上浏览器测试。

Cloudflare Tunnel 仅作为有实际Cloud运行/访问阻碍时的备选评估：若指向用户电脑上的Streamlit，电脑必须持续开机，因此不能满足独立在线启动要求。不得将它冒充本方案等价交付；未启动隧道，未引入付费服务。
