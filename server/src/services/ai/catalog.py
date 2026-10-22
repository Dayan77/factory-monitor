"""Tool catalog of the AI assistant — a copy of the `ai-tools` block of docs/ai_assistant.md §4 (the server image
does not ship docs/). `server/tests/test_ai_catalog.py` asserts this copy equals the spec, so a change in the doc
must be mirrored here (and the other way round)."""
from __future__ import annotations

import json
from typing import Any

TOOLS: list[dict[str, Any]] = json.loads(r"""
[
 {
  "name": "resolve_asset",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Encontra planta, linha, máquina ou posição pelo nome falado pelo usuário e devolve ids. Use antes de qualquer outra ferramenta quando o usuário citar um ativo pelo nome.",
  "api": [
   {
    "method": "GET",
    "path": "/api/plants/tree"
   },
   {
    "method": "GET",
    "path": "/api/positions/"
   }
  ],
  "params": {
   "query": {
    "type": "string"
   },
   "kind": {
    "type": "string",
    "enum": [
     "plant",
     "line",
     "machine",
     "position",
     "any"
    ]
   }
  },
  "required": [
   "query"
  ],
  "link": null
 },
 {
  "name": "get_machine_status",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Estado atual (rodando, parada, falha, manutenção, sem dados) das máquinas, com a parada aberta e há quanto tempo.",
  "api": [
   {
    "method": "GET",
    "path": "/api/overview"
   },
   {
    "method": "GET",
    "path": "/api/plants/tree"
   }
  ],
  "params": {
   "plant_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "machine_id": {
    "type": "string"
   }
  },
  "required": [],
  "link": "/factory/machines/{machine_id}"
 },
 {
  "name": "get_oee",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "OEE (ISO 22400) e disponibilidade, desempenho e qualidade de máquina, linha ou planta no período, opcionalmente por hora, turno ou dia.",
  "api": [
   {
    "method": "GET",
    "path": "/api/oee"
   }
  ],
  "params": {
   "scope": {
    "type": "string",
    "enum": [
     "machine",
     "line",
     "plant"
    ]
   },
   "id": {
    "type": "string"
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string",
    "format": "date-time"
   },
   "stop": {
    "type": "string",
    "format": "date-time"
   },
   "group_by": {
    "type": "string",
    "enum": [
     "none",
     "hour",
     "shift",
     "day",
     "shift_name"
    ]
   },
   "compare_to": {
    "type": "string",
    "enum": [
     "none",
     "previous_period"
    ]
   }
  },
  "required": [
   "scope",
   "id"
  ],
  "link": "/oee?scope={scope}&id={id}&start={start}&stop={stop}&group_by={group_by}"
 },
 {
  "name": "get_losses_pareto",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Pareto das perdas de tempo (paradas por motivo, micro-paradas, velocidade, qualidade) em minutos e % do total.",
  "api": [
   {
    "method": "GET",
    "path": "/api/losses/pareto"
   }
  ],
  "params": {
   "scope": {
    "type": "string",
    "enum": [
     "machine",
     "line",
     "plant"
    ]
   },
   "id": {
    "type": "string"
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   },
   "by": {
    "type": "string",
    "enum": [
     "code",
     "category"
    ]
   }
  },
  "required": [
   "scope",
   "id"
  ],
  "link": "/oee?scope={scope}&id={id}&start={start}&stop={stop}&tab=pareto"
 },
 {
  "name": "get_downtime",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Lista de paradas (≥ limiar) com máquina, início, duração, motivo; filtros de abertas e sem motivo.",
  "api": [
   {
    "method": "GET",
    "path": "/api/downtime/"
   }
  ],
  "params": {
   "machine_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   },
   "open": {
    "type": "boolean"
   },
   "unjustified": {
    "type": "boolean"
   },
   "limit": {
    "type": "integer",
    "maximum": 50
   }
  },
  "required": [],
  "link": "/stops?line_id={line_id}&machine_id={machine_id}&start={start}&stop={stop}"
 },
 {
  "name": "get_shift_report",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Relatório de turno de uma linha: OEE, Pareto, paradas e produção por máquina.",
  "api": [
   {
    "method": "GET",
    "path": "/api/reports/shift"
   }
  ],
  "params": {
   "line_id": {
    "type": "string"
   },
   "productive_day": {
    "type": "string",
    "format": "date"
   },
   "shift_id": {
    "type": "string"
   }
  },
  "required": [
   "line_id",
   "productive_day"
  ],
  "link": "/reports/shift?line_id={line_id}&day={productive_day}&shift_id={shift_id}"
 },
 {
  "name": "get_component_life",
  "status": "existing",
  "roles": [
   "maintenance_planner",
   "manager",
   "technician",
   "production_supervisor"
  ],
  "description": "Componentes instalados com % de vida consumida (medida determinante), RUL em dias e data projetada, e alerta de vida.",
  "api": [
   {
    "method": "GET",
    "path": "/api/maintenance/components"
   }
  ],
  "params": {
   "plant_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "min_life": {
    "type": "number"
   }
  },
  "required": [],
  "link": "/maintenance/components"
 },
 {
  "name": "get_maintenance_due",
  "status": "existing",
  "roles": [
   "maintenance_planner",
   "manager",
   "technician",
   "production_supervisor"
  ],
  "description": "Planos de manutenção com estado (ok, due_soon, due, overdue), % do intervalo, gatilho e data de vencimento (o que vier primeiro).",
  "api": [
   {
    "method": "GET",
    "path": "/api/maintenance-plans/status"
   }
  ],
  "params": {
   "plant_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "machine_id": {
    "type": "string"
   },
   "state": {
    "type": "array",
    "items": {
     "type": "string",
     "enum": [
      "ok",
      "due_soon",
      "due",
      "overdue"
     ]
    }
   }
  },
  "required": [],
  "link": "/maintenance/upcoming"
 },
 {
  "name": "get_work_orders",
  "status": "existing",
  "roles": [
   "*"
  ],
  "description": "Ordens de serviço por estado, tipo, técnico ('me' = o próprio usuário), máquina/linha, vencidas e período.",
  "api": [
   {
    "method": "GET",
    "path": "/api/work-orders/"
   }
  ],
  "params": {
   "state": {
    "type": "array",
    "items": {
     "type": "string"
    }
   },
   "type": {
    "type": "string",
    "enum": [
     "preventive",
     "predictive",
     "corrective",
     "improvement"
    ]
   },
   "assignee_id": {
    "type": "string"
   },
   "machine_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "plant_id": {
    "type": "string"
   },
   "overdue": {
    "type": "boolean"
   },
   "from": {
    "type": "string",
    "format": "date"
   },
   "to": {
    "type": "string",
    "format": "date"
   },
   "completed_from": {
    "type": "string"
   },
   "completed_to": {
    "type": "string"
   },
   "limit": {
    "type": "integer",
    "maximum": 50
   }
  },
  "required": [],
  "link": "/maintenance/work-orders?wo={id}"
 },
 {
  "name": "get_maintenance_kpis",
  "status": "existing",
  "roles": [
   "maintenance_planner",
   "manager",
   "production_supervisor"
  ],
  "description": "PMP, cumprimento da programação (com exclusões e motivo) e backlog em horas e semanas, no período.",
  "api": [
   {
    "method": "GET",
    "path": "/api/maintenance/kpis"
   }
  ],
  "params": {
   "plant_id": {
    "type": "string"
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   }
  },
  "required": [],
  "link": "/maintenance/kpis?start={start}&stop={stop}"
 },
 {
  "name": "get_reliability",
  "status": "existing",
  "roles": [
   "maintenance_planner",
   "manager",
   "production_supervisor"
  ],
  "description": "MTBF (h), MCBF (ciclos), MTTR (min) com n, restabelecidas e incipientes; MTTF por fabricante/modelo com falhas e suspensões. Janela padrão 12 meses.",
  "api": [
   {
    "method": "GET",
    "path": "/api/maintenance/reliability"
   }
  ],
  "params": {
   "group_by": {
    "type": "string",
    "enum": [
     "position",
     "machine",
     "component_type",
     "manufacturer",
     "manufacturer_model"
    ]
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   },
   "plant_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   }
  },
  "required": [],
  "link": "/maintenance/reliability?group_by={group_by}"
 },
 {
  "name": "search_failures",
  "status": "existing",
  "roles": [
   "maintenance_planner",
   "manager",
   "technician",
   "production_supervisor"
  ],
  "description": "Falhas registradas (ISO 14224: modo, mecanismo, causa, detecção, severidade) com início, restabelecimento e OS ligada.",
  "api": [
   {
    "method": "GET",
    "path": "/api/failures/"
   }
  ],
  "params": {
   "machine_id": {
    "type": "string"
   },
   "line_id": {
    "type": "string"
   },
   "plant_id": {
    "type": "string"
   },
   "position_id": {
    "type": "string"
   },
   "component_id": {
    "type": "string"
   },
   "severity": {
    "type": "string",
    "enum": [
     "critical",
     "degraded",
     "incipient"
    ]
   },
   "open": {
    "type": "boolean"
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   },
   "failure_mode": {
    "type": "string"
   },
   "q": {
    "type": "string"
   },
   "limit": {
    "type": "integer",
    "maximum": 50
   }
  },
  "required": [],
  "link": "/maintenance/failures?f={id}"
 },
 {
  "name": "get_predictive_alerts",
  "status": "sprint5",
  "roles": [
   "maintenance_planner",
   "manager",
   "technician",
   "production_supervisor"
  ],
  "description": "Alertas de condição (preditiva): sinal, severidade, texto explicável, RUL de condição com faixa, estado e OS ligada.",
  "api": [
   {
    "method": "GET",
    "path": "/api/predictive/alerts"
   }
  ],
  "params": {
   "state": {
    "type": "array",
    "items": {
     "type": "string",
     "enum": [
      "open",
      "acknowledged",
      "converted",
      "closed"
     ]
    }
   },
   "severity": {
    "type": "string",
    "enum": [
     "watch",
     "warning",
     "critical"
    ]
   },
   "line_id": {
    "type": "string"
   },
   "position_id": {
    "type": "string"
   }
  },
  "required": [],
  "link": "/maintenance/predictive?alert={id}"
 },
 {
  "name": "get_condition_trend",
  "status": "sprint5",
  "roles": [
   "maintenance_planner",
   "manager",
   "technician",
   "production_supervisor"
  ],
  "description": "Série do sinal de condição de uma posição com linha de base, limites, EWMA/CUSUM e RUL de condição.",
  "api": [
   {
    "method": "GET",
    "path": "/api/predictive/monitors"
   },
   {
    "method": "GET",
    "path": "/api/predictive/features"
   }
  ],
  "params": {
   "position_id": {
    "type": "string"
   },
   "feature": {
    "type": "string",
    "enum": [
     "stroke_time",
     "current",
     "vibration",
     "temperature"
    ]
   },
   "period": {
    "type": "string"
   },
   "start": {
    "type": "string"
   },
   "stop": {
    "type": "string"
   }
  },
  "required": [
   "position_id"
  ],
  "link": "/maintenance/predictive?position_id={position_id}&feature={feature}"
 }
]
""")

BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOLS}
