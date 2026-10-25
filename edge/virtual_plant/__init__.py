"""
Planta virtual "Planta Manaus" — CLPs simulados (OPC UA + Modbus TCP) para testes de integração.

- `layout`  : a planta (linhas, máquinas, posições, componentes, sinais e endereços) e o mapa do gateway.
- `model`   : simulação determinística (seed + passo fixo de tempo simulado), sem I/O.
- `runtime` : servidores OPC UA (asyncua) e Modbus TCP (pymodbus) + endpoint HTTP de estado.

Uso: `python -m edge.virtual_plant --help` (no container: `python -m virtual_plant`).
Este pacote não importa nada de `edge.src`: roda sozinho no container do edge.
"""
