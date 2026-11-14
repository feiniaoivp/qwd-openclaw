# NVIDIA Provider 模型可用性探测报告

**探测时间**: 2026-11-12 05:15
**探测账户**: `nvidia-2` (key 前缀 `nvapi-K0Fdgj`, len=70)
**Base URL**: `https://integrate.api.nvidia.com/v1`
**探测方法**: 直接 `POST /chat/completions`，max_tokens=40~300，逐模型实测

---

## 一、结论速览

| 指标 | 数量 |
|---|---|
| `/models` 端点返回模型总数 | **84** |
| 配置中登记的模型 | 5 |
| **实测可调用** | **4** |
| 404（账户无该 Function 权限） | 60+ |
| 410 Gone（EOL 已下线） | 2 |
| 超时不可用 | 4 |
| **配置的 5 个中可用** | **0** ⚠️ |

---

## 二、配置中 5 个模型的逐个判定

| 模型 | 状态 | 错误详情 |
|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | ❌ **404** | `Function 'ac74040f-9fc9-4c5e-ac74-279ba5161d69': Not found for account 'VjdolzuDgRGkcgot48EHwJwtL4Q5HPHnArSGNEoBonI'` |
| `nvidia/nemotron-3-ultra-550b-a55b` | ⚠️ **空响应** | HTTP 200，但 `content=null`，正文全在 `reasoning_content` 且为乱码（`' tyLaunchHH1Arab'`, `' Ren SpeciesReviewergen SAT allowsrh com'`）。推理模型正文通道失效。 |
| `moonshotai/kimi-k3` | ❌ **410 EOL** | `The model 'moonshotai/kimi-k3' has reached its end of life` |
| `z-ai/glm-5.3` | ❌ **410 EOL** | `The model 'z-ai/glm-5.3' has reached its end of life on 2026...` |
| `z-ai/glm-5.3-flash` | ❌ **超时** | 26~60s 读超时，不可用 |

**5 个全部不可用。**

---

## 三、账户实际可用的模型（4 个）

| 模型 | 延迟 | 实测返回 |
|---|---|---|
| `deepseek-ai/deepseek-v4.1-flash` | **4.3s** | `'OK'` ✅ |
| `mistralai/mistral-large-4-1t-a52b` | **3.0s** | `'OK'` ✅ |
| `nvidia/riva-translate-4b-instruct-v2` | **1.4s** | `'say OK'` ✅ |
| `minimaxai/minimax-m3.1-flash` | 17.4s | `'OK'` ✅（偏慢） |

**推荐替代**：`deepseek-ai/deepseek-v4.1-flash`（4.3s，最快最稳）

---

## 四、关键发现：`/models` 元数据严重滞后

`/models` 端点返回 84 个模型，但**实际可调用的只有 4 个**。绝大多数返回 404：

```
ai21labs/jamba-1.5-large-instruct          404
aisingapore/sea-lion-7b-instruct           404
databricks/dbrx-instruct                   404
deepseek-ai/deepseek-coder-6.7b-instruct   404
google/gemma-3-12b-it                      404
google/gemma-3-4b-it                       404
ibm/granite-3.0-3b-a800m-instruct          404
ibm/granite-3.0-8b-instruct                404
... 共 60+ 个
```

**推断**：该账户（`VjdolzuDgRGkcgot48EHwJwtL4Q5HPHnArSGNEoBonI`）的 API 权益已大幅收缩，NVIDIA 的 `/models` 列表未同步账户实际权限。

---

## 五、对系统的实际影响

### 1. Fallback 链中 3 项已死

当前 fallback 链（10 项）：
```
1. nvidia/z-ai/glm-5.3-flash            ❌ 超时
2. amd-radeon/Qwen3.8-Flash-Next        未测
3. amd-radeon/DeepSeek-V4-Flash         ✅ 683ms
4. amd-radeon/MiMo-V2.6-Flash           未测
5. deepseek/deepseek-chat               ⚠️ models status 报不在目录
6. deepseek/deepseek-reasoner           ⚠️ models status 报不在目录
7. nvidia/nemotron-3-super-120b-a12b    ❌ 404
8. nvidia/nemotron-3-ultra-550b-a55b    ⚠️ 空响应
9. openrouter/nvidia/nemotron-3-super-120b-a12b:free   未测
10. openrouter/nvidia/nemotron-3.5-lightning:free      未测
```

### 2. 已造成实际故障

`汇川技术` 会话（sessionId `a8b7d8df-ba44-4e97-b393-42d865b9ffaf`）日志：
```
empty response retries exhausted: provider=nvidia/nvidia/nemotron-3-ultra-550b-a55b attempts=1/1
incomplete turn detected: stopReason=stop hasLastAssistant=yes
```

该会话使用 `nemotron-3-ultra-550b-a55b`，因 `content=null` 导致回合中断 → 这正是「对话没恢复回来」的直接原因。

### 3. 默认主模型不受影响

`amd-radeon/Gemma-4-31B-IT` 走 AMD，实测正常（AMD provider 683ms 响应）。

---

## 六、修复方案

### 方案 A：替换 NVIDIA 模型清单（推荐）

将 `nvidia` 和 `nvidia-2` 两个 provider 的 models 改为实测可用的：

```json
{
  "id": "deepseek-ai/deepseek-v4.1-flash",
  "name": "DeepSeek V4.1 Flash (NVIDIA)",
  "contextWindow": 128000
},
{
  "id": "mistralai/mistral-large-4-1t-a52b",
  "name": "Mistral Large 4 (NVIDIA)",
  "contextWindow": 128000
}
```

### 方案 B：清理 fallback 链（最小改动）

移除 3 个死的 NVIDIA 项，链变为 7 项：
```
amd-radeon/Qwen3.8-Flash-Next
amd-radeon/DeepSeek-V4-Flash
amd-radeon/MiMo-V2.6-Flash
deepseek/deepseek-chat
deepseek/deepseek-reasoner
openrouter/nvidia/nemotron-3-super-120b-a12b:free
openrouter/nvidia/nemotron-3.5-lightning:free
```

### 方案 C：整个下线 nvidia / nvidia-2 provider

若 NVIDIA 权益已不打算续，直接移除两个 provider，靠 AMD + DeepSeek + OpenRouter 兜底。

---

## 七、待办

- [ ] 修复 `汇川技术` 会话的模型引用
- [ ] 清理 fallback 链中的 3 个死项
- [ ] 决定 NVIDIA provider 去留
- [ ] 验证 4 个替代模型在多轮工具调用场景下的稳定性
