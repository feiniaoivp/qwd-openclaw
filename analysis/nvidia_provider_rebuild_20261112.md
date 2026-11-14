# NVIDIA Provider 重建记录

**执行时间**: 2026-11-12 05:19 ~ 05:40
**操作**: 重建 `nvidia` 与 `nvidia-2` 两个 provider 的模型清单

---

## 一、重建前状态

两个 provider 的模型清单**完全相同**，共 5 个，**全部不可用**：

| 模型 | 判定 |
|---|---|
| `nvidia/nemotron-3-ultra-550b-a55b` | ⚠️ HTTP 200 但 `content=null`，正文全乱码进 reasoning_content |
| `nvidia/nemotron-3-super-120b-a12b` | ❌ 404 `Function not found for account` |
| `moonshotai/kimi-k3` | ❌ 410 Gone（EOL 已下线） |
| `z-ai/glm-5.3` | ❌ 410 Gone（EOL 已下线） |
| `z-ai/glm-5.3-flash` | ❌ 26~60s 超时 |

---

## 二、探测方法

直接 `POST https://integrate.api.nvidia.com/v1/chat/completions`，绕过 OpenClaw 层，
对 `/models` 返回的全部 84 个模型做逐条实测。

**关键教训**：`/models` 端点返回 84 个模型，但**实际可调用的只有 4 个**。
NVIDIA 的模型元数据严重滞后于账户实际权限，不能信任 `/models` 列表。

---

## 三、重建后清单（3 个实测可用模型）

两个 provider 采用相同清单：

| 模型 ID | 显示名 | reasoning | contextWindow | maxTokens | 实测延迟 |
|---|---|---|---|---|---|
| `deepseek-ai/deepseek-v4.1-flash` | DeepSeek V4.1 Flash (NVIDIA) | **true** | 131072 | 32768 | 10.6s（简单）/ 4.3s（极简） |
| `mistralai/mistral-large-4-1t-a52b` | Mistral Large 4 (NVIDIA) | **true** | 131072 | 32768 | 24.5s（简单）/ 3.0s（极简） |
| `minimaxai/minimax-m3.1-flash` | MiniMax M3.1 Flash (NVIDIA) | false | 131072 | 16384 | 31.5s（业务提示） |

### 关键配置决策：`reasoning: true`

初版把三个模型都标成 `reasoning: false`，结果中文提示下 `content` 恒为 null。
深入排查发现：

```
deepseek-v4.1-flash:
  max_tokens=60  → content=null, reasoning_tokens 占满
  max_tokens=300 → content='收到，链路正常。', reasoning_tokens=145

mistral-large-4:
  max_tokens=60  → content=null
  max_tokens=300 → content=null, reasoning_tokens=299 (finish=length)
  max_tokens=800 → content='由于我无法获取实时行情...', reasoning_tokens=712
```

**两个模型都是推理模型，reasoning_tokens 会吃掉绝大部分输出预算。**
必须标 `reasoning: true`（OpenClaw 才会正确处理）并给足 `maxTokens`（32768）。

`minimax-m3.1-flash` 实测直接出 content，标 `reasoning: false` 即可。

---

## 四、验证结果

### 配置层
```
$ openclaw config validate
Config valid: ~/.openclaw/openclaw.json
```

### 模型注册层
```
$ openclaw models list | grep nvidia
nvidia/deepseek-ai/deepseek-v4.1-flash     text  131k
nvidia/minimaxai/minimax-m3.1-flash        text  131k
nvidia/mistralai/mistral-large-4-1t-a52b   text  131k
nvidia-2/deepseek-ai/deepseek-v4.1-flash     text  131k
nvidia-2/minimaxai/minimax-m3.1-flash        text  131k
nvidia-2/mistralai/mistral-large-4-1t-a52b   text  131k
```
6 条全部注册成功。

### 实际调用层（业务提示）
```
deepseek-ai/deepseek-v4.1-flash     ✅ 10.6s  '银行、医药生物。'
mistralai/mistral-large-4-1t-a52b   ✅ 24.5s  '以下是两个常见的A股行业名称：1. 银行 2. 医药生物'
minimaxai/minimax-m3.1-flash        ✅ 31.5s  '我无法获取实时行情，今天可重点观察政策...'
```

---

## 五、⚠️ 性能警告

**这三个 NVIDIA 模型的延迟（10~32 秒）远高于 AMD Radeon。**

| Provider | 典型延迟 |
|---|---|
| `amd-radeon/DeepSeek-V4-Flash` | **683ms** |
| `nvidia/deepseek-ai/deepseek-v4.1-flash` | 10,620ms |
| `nvidia/mistralai/mistral-large-4-1t-a52b` | 24,488ms |
| `nvidia/minimaxai/minimax-m3.1-flash` | 31,508ms |

**定位建议**：
- **主模型**：继续用 AMD（快 15~45 倍）
- **NVIDIA 角色**：只做 fallback 末位，或在 AMD 全线不可用时兜底
- **不要**把 NVIDIA 设为主模型或前位 fallback

---

## 六、遗留问题（未处理）

1. **fallback 链仍有 3 个死项**：
   - `nvidia/z-ai/glm-5.3-flash`（超时）
   - `nvidia/nemotron-3-super-120b-a12b`（404）
   - `nvidia/nemotron-3-ultra-550b-a55b`（空响应）

   注意：`nvidia` provider 的 models 清单已重建，但**fallback 链是独立配置**
   （`agents.defaults.model.fallbacks`），里面仍指向旧模型 ID。

2. **「汇川技术」会话仍绑定死模型**：
   ```
   sessionId: a8b7d8df-ba44-4e97-b393-42d865b9ffaf
   modelOverride: nvidia/nemotron-3-ultra-550b-a55b
   ```
   该模型已从 provider 清单移除，会话可能报「模型不存在」。

3. **老 `nvidia` provider 的 key 未验证**：
   因 state 库独占锁，无法读取 auth store 里的 key。假设与 `nvidia-2` 权益一致
   （两者模型清单原本完全相同）。

---

## 七、变更文件

- `~/.openclaw/openclaw.json` — 两个 provider 的 `models` 数组
- 备份：`~/.openclaw/openclaw.json.bak-nvidia-20261112-051927`

## 八、回滚方法

```bash
cp ~/.openclaw/openclaw.json.bak-nvidia-20261112-051927 ~/.openclaw/openclaw.json
```

---

## 九、遗留问题闭环（2026-11-12 13:58 补）

1. ✅ **fallback 链已清理**：移除 3 个死项（glm-5.3-flash / nemotron-3-super / nemotron-3-ultra），
   末位追加 `nvidia-2/deepseek-ai/deepseek-v4.1-flash`。
   新链：amd-radeon/DeepSeek-V4-Flash → MiMo-V2.6-Flash → deepseek-chat → deepseek-reasoner → nvidia-2/deepseek-v4.1-flash
2. ✅ **「汇川技术」会话已修复**：`sessions patch model=default`，
   覆盖从 `nvidia/nemotron-3-ultra-550b-a55b` 重置为默认 `amd-radeon/Gemma-4-31B-IT`（实测 901ms 可用）。
   （session_nodes 中 modelOverrideSource 已变为 default）
