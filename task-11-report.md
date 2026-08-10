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
    6 passed

    bash scripts/security-scan.sh
    通过

    git diff --check
    通过

覆盖的行为包括：伪造身份头被忽略、v2 签名篡改被拒绝、两用户结果隔离、一次
底层生成只形成一次用量事件、固定 Canvas mount、复制排除生成子应用/状态/密钥
形态文件、符号链接拒绝、已有目标拒绝以及补丁不匹配时的失败清理。

## 明确未做

未读写、启动、停止、探测或连接
`/Users/260413a/ai-generation-portable-apps`；未连接 `9090`、`8787`、`8797`
或任何真实测试端口；未操作 launchd。真实 `9190/8992/8798/8788/8892`
冒烟测试和把补丁应用到任何 Portal 副本，都等待用户明确批准并由用户提供独立测试实例。

## 残余风险

补丁只接受上下文完全匹配的 Portal `app.py` 形状；实际 Portal 的认证代理
调度点若不同，准备脚本会安全失败而不会尝试模糊应用。真实 Portal 测试副本的
版本与其现有会话代理集成，需要在后续获授权的隔离烟雾阶段确认。
