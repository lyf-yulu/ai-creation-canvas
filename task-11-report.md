# Task 11 — Portal 薄代理测试集成报告

## 交付范围

- `integrations/portal/`：仅含 Canvas mount 元数据、可审阅 v2 身份补丁和操作说明。
- `scripts/prepare-portal-test-copy.sh`：只接受绝对路径；只在本仓库
  `work/portal-test-*` 创建全新测试副本；严格允许列表复制并在补丁失败时清理。
- `tests/integration/test_portal_contract.py`：仅使用临时合成夹具和进程内
  Canvas 应用，不启动或连接真实 Portal、生成服务或测试/生产端口。

## 验证证据

已运行：

    PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_portal_contract.py
    9 passed

    bash scripts/security-scan.sh
    通过

    git diff --check
    通过

第 1 轮 RED：补丁只含辅助函数、静态敏感文件会被复制，且测试没有执行补丁后的
Portal 路由。第 1 轮 GREEN：补丁现已向合成固定 Portal 基线注册实际
/ai-canvas/ 和嵌套路由，固定转发到 127.0.0.1:8992，从已认证会话重签
身份，并返回真实 Canvas 响应。

第 2 轮 RED：认证 hook/签名配置、动态 Connection 头与 Portal 计量边界未被
明确验证。第 2 轮 GREEN：启动阶段显式校验合成固定 Portal 的认证 hook 和
最短签名 token；未知 X-Portal-*、Connection token 和 hop-by-hop 请求/响应
头均被过滤；用量由模拟 Portal 既有下游记录器精确记录一次，代理不添加计量头。

覆盖的行为包括：伪造全部身份头被替换、v2 签名验证的篡改/过期拒绝、两用户
经真实 Canvas 应用的任务隔离、一次生成只产生一次底层适配器调用、mount 前缀
重写、方法/正文/Cookie 转发、SPA 回退、编码路径和开放代理拒绝、复制排除生成
子应用/状态/任意深度敏感文件、任意符号链接拒绝、已有目标保留以及补丁不匹配
时的失败清理。

## 明确未做

未读写、启动、停止、探测或连接
`/Users/260413a/ai-generation-portable-apps`；未连接 `9090`、`8787`、`8797`
或任何真实测试端口；未操作 launchd。真实 `9190/8992/8798/8788/8892`
冒烟测试和把补丁应用到任何 Portal 副本，都等待用户明确批准并由用户提供独立测试实例。

## 残余风险

补丁只接受上下文完全匹配的 Portal `app.py` 形状；实际 Portal 的认证代理
调度点若不同，准备脚本会安全失败而不会尝试模糊应用。真实 Portal 测试副本的
版本与其现有会话代理集成，需要在后续获授权的隔离烟雾阶段确认。
