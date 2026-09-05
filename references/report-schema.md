# 机器可读报告格式

新审计使用 `sieve-audit/v2`。校验器继续接受已有 v1 报告，但不得再生成 v1。

## 顶层结构

```json
{
  "schema_version": "sieve-audit/v2",
  "target": {"path": "~/repos/example-skill", "name": "example-skill"},
  "audit_context": {
    "model": "unknown",
    "provider": "unknown",
    "model_version": "unknown",
    "audited_at": "2026-09-04T12:00:00+08:00"
  },
  "summary": {
    "recommendation": "needs_comparison",
    "confidence": "low",
    "counts": {
      "asset_types": {
        "generic": 0,
        "temporary": 0,
        "proprietary": 0,
        "mixed": 0,
        "unknown": 1,
        "not_applicable": 0
      },
      "artifact_roles": {
        "instruction": 1,
        "reference": 0,
        "source": 0,
        "test": 0,
        "asset": 0,
        "metadata": 0,
        "generated": 0,
        "binary": 0,
        "secret": 0,
        "historical": 0
      }
    },
    "note": "规则块构成，不代表科学精度。"
  },
  "items": [],
  "human_decisions": [],
  "not_performed": ["未修改、移动、归档或删除目标 Skill"]
}
```

## 单项结构

```json
{
  "id": "rule-001",
  "source": {"path": "SKILL.md", "line": 12, "section": "工作流"},
  "summary": "规则的最小必要摘要",
  "asset_type": "unknown",
  "artifact_role": "instruction",
  "lifecycle": "needs_test",
  "replacement_evidence": {
    "level": "E1",
    "basis": "只读文本判断，没有对照。"
  },
  "behavior_validation": {
    "status": "untested",
    "evidence": "",
    "checks_run": 0
  },
  "dependency": {
    "state": "entrypoint",
    "external_callers_checked": false
  },
  "confidence": "low",
  "reason": "没有无 Skill 对照。",
  "recommendation": "needs_comparison",
  "requires_human_approval": true,
  "scope": {
    "models_tested": [],
    "tasks_tested": [],
    "not_verified": ["所有真实任务"]
  }
}
```

## 一致性要求

- 两组 `summary.counts` 必须与 `items` 实际数量一致。
- `secret`、`binary`、`generated` 必须使用 `asset_type=not_applicable`。
- `replacement_evidence.level=E2/E3` 必须提供 `comparison_id`。
- `behavior_validation.status=passed/failed` 必须提供实际证据，且 `checks_run>=1`；测试命令退出码为 0 但实际收集 0 项，不得记为 `passed`。
- `archive_candidate` 必须同时具备 E3、外部调用检查和人工确认。
- `secret` 只能建议 `keep` 或 `human_review`。
- 所有建议都必须 `requires_human_approval=true`。
- 模型未知时，总体和逐项置信度不得超过 `low`。
- `not_performed` 必须明确没有修改、移动、归档或删除目标 Skill。
- 正式报告用 `validate_report.py <report.json> --skill-directory <目录>` 校验证据路径和行号，不能只校验 JSON 结构。
