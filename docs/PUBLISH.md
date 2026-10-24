# 发布到 GitHub：从零开始

> 假设你从没用过 GitHub。每一步都能照抄。
> 你这台机器的现状：`gh` 已安装、Git 仓库已初始化、已有 4 个提交、**但 gh 的登录凭证失效了**。

---

## 一、先搞懂四个词

| 词 | 说人话 |
|---|---|
| **Git** | 一个记录「每次改了什么都存一份」的工具，装在你电脑上 |
| **commit** | 一次存档。你已经有 4 个了 |
| **GitHub** | 一个网站，帮你把这些存档放到云上，别人也能看见 |
| **push** | 把本地的存档上传到 GitHub |

关系：**Git（本地）→ push → GitHub（云端）**。

你现在的情况：本地有 4 个存档，但**还没有云端地址**（`git remote` 是空的）。

---

## 二、项目名怎么起

### 2.1 先破除一个误解

**GitHub 仓库名只需要在你自己的账号下唯一。**别人叫 `vlearn` 不影响你叫 `vlearn`。
所以名字不必纠结——**而且随时能在设置里改，URL 会自动跳转**。

真正需要全局唯一的是 **PyPI 包名**（如果你以后想让别人 `pip install vlearn`）。
这个我帮你查过了：

| 名字 | GitHub 仓库 | PyPI 包名 |
|---|---|---|
| `vlearn` | 随便用 | ✅ 可用 |
| `vlearn-cli` | 随便用 | ✅ 可用 |
| `video-to-notes` | 随便用 | ✅ 可用（但线上已有一个同名工具，容易被搞混） |

### 2.2 硬性规则

- 只能用：字母、数字、`-`、`_`、`.`
- 不能有空格、不能有中文
- 长度 1~100 字符
- 建议**全小写 + 连字符**，因为 URL 里大小写不敏感，写 `VLearn` 和 `vlearn` 会让人困惑

### 2.3 起名公式

```
<对象>-<动作>      video-to-notes、audio-summary
<形容词>-<名词>    fast-notes、smart-cut
<单词>            vlearn、clipy
```

**推荐用 `vlearn`**：短、好念、PyPI 可用、跟你项目目录名一致。

### 2.4 一点审美建议

- 别用 `my-project`、`test`、`demo` —— 一看就是练手，别人不会点
- 别用 `-ai` 结尾堆热词 —— 2026 年这个后缀已经不说明什么了
- 描述里写清楚**它解决什么问题**，比名字重要 10 倍

---

## 三、第一步：重新登录

你的 `gh` token 已经失效，先修好：

```bash
gh auth login
```

它会问几个问题，这样选：

| 问题 | 选什么 |
|---|---|
| What account do you want to log into? | **GitHub.com** |
| What is your preferred protocol for Git operations? | **HTTPS**（更简单，不用配 SSH key） |
| Authenticate Git with your GitHub credentials? | **Yes** |
| How would you like to authenticate? | **Login with a web browser** |
| 屏幕出现 8 位代码 | 复制它，按回车，浏览器里粘贴并授权 |

验证：

```bash
gh auth status
# 应该看到 ✓ Logged in to github.com account yfandragon
```

---

## 四、第二步：创建仓库并推送

### 路线 A：命令行一条命令（推荐）

```bash
cd /Users/duanyifan/Environment/project/learn/videoFastLearn

# --source=. 用当前目录，--push 建完就推，--public 公开
gh repo create vlearn --public --source=. --remote=origin --push \
  --description "把视频链接变成带时间戳的中文笔记，复用 opencode Go / Codex 的订阅额度"
```

然后打开 `https://github.com/yfandragon/vlearn` 看看。

### 路线 B：网页创建（不想用命令行）

1. 打开 <https://github.com/new>
2. Repository name 填 `vlearn`
3. Description 填上面那句话
4. 选 **Public**
5. **不要**勾「Add a README file」「.gitignore」「license」——你本地都有了，勾了会冲突
6. 点 **Create repository**
7. 回到终端，执行它页面提示的「push an existing repository」那三行：

```bash
git remote add origin https://github.com/yfandragon/vlearn.git
git branch -M main
git push -u origin main
```

如果提示要密码，**密码处填 Personal Access Token**（不是账号密码）：
GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token，
勾 `repo` 权限，复制生成的 `ghp_...`。用 `gh auth login` 的话不需要这一步。

---

## 五、第三步：发布后该做的五件事

### 5.1 设置仓库信息

页面上点 **⚙️ 齿轮**（About 右边）：

| 字段 | 填什么 |
|---|---|
| Description | 把视频链接变成带时间戳的中文笔记，复用 opencode Go / Codex 的订阅额度 |
| Website | 留空（除非你做了主页） |
| Topics | `video` `summary` `notes` `subtitle` `whisper` `opencode` `codex` `cli` `python` |

Topics 很重要：别人搜 `video summary` 时能不能找到你，主要看这个。

### 5.2 打版本标签

```bash
git tag -a v0.4.0 -m "v0.4.0: 网页界面 + 模型直连下载"
git push --tags
```

然后在仓库页面 **Releases → Draft a new release** → 选 `v0.4.0` → 标题写 `v0.4.0` →
正文抄 README 的「已知限制」→ Publish。

### 5.3 检查 README 首屏

GitHub 上打开你的仓库，**只看第一屏**（不滚动）。能不能让陌生人 10 秒内明白：

- 这是干嘛的？✅ 有一句话说明 + 代码示例
- 怎么用？✅ 有快速开始
- 需要什么？✅ 有前置条件

如果第一屏全是徽章和目录，删掉——没人看。

### 5.4 确认许可证

`LICENSE` 已有（MIT）。GitHub 会自动在仓库右侧显示 "MIT license"。
注意：**MIT 意味着别人可以拿去商用**。如果想限制，换 Apache-2.0（要保留版权声明）或 AGPL-3.0。

### 5.5 交点朋友

- 在 README 里链接你参考过的项目（比如 video-to-notes），这是礼貌也是 SEO
- 给那些项目提 issue/PR，比自顾自发布更容易被看见

---

## 六、安全检查清单（发布前必看）

```bash
git ls-files | grep -iE "auth|cookie|secret|\.env|token"
```

**应该没有任何输出。** 如果有，说明密钥要被公开了，立刻处理。

其他检查：

- [ ] `.gitignore` 里有 `out/`、`.venv/`、`.hf/`、`__pycache__/`（已配好）
- [ ] 没有提交超过 50MB 的文件：`git ls-files -s | ...` 或直接看仓库大小
- [ ] README 里没有你的私人路径（`/Users/duanyifan/...`）
- [ ] `meta.json` 里没有敏感信息（现在只有标题/作者/时长）

已经在提交里的敏感文件怎么删？如果还没 push，`git rm --cached 文件` 再提交即可；
**已经 push 了就必须把 token/key 作废重签**（因为历史里永久留着）。

---

## 七、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `remote origin already exists` | 已经加过远端 | `git remote set-url origin <新地址>` |
| `Authentication failed` | 用了账号密码 | 改用 `gh auth login` 或 Personal Access Token |
| push 上去发现少文件 | 被 `.gitignore` 忽略了 | `git check-ignore -v 文件路径` 看是哪条规则 |
| 想改仓库名 | — | 设置页 Rename；GitHub 会自动重定向旧地址 |
| 想从私有改公开 | — | Settings → 最下方 Danger Zone → Change visibility |
| 后悔公开了 | — | 同上，但**已经被人 clone 的收不回来** |
| `gh: command not found` | 没装 | `brew install gh` |

---

## 八、发布之后

**别急着加功能。**先做这三件事：

1. **自己按 README 从零装一遍**（新建一个目录、删掉 `.venv`、照抄命令）。
   你会发现至少 3 处文档没写清楚的地方——这就是最有价值的反馈。
2. **找一个真实视频跑通**，把输出贴到 README 的 examples 里。
3. **看一周内有没有人提 issue**。有人问「怎么用」不是他们的问题，是文档的问题。

这三个做完，比再加十个功能更能让项目活下来。
