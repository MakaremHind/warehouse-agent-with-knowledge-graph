# Warehouse Agent with Knowledge Graph

This repository contains two versions of a warehouse control agent:
1. **Agent without Knowledge Graph (baseline)**
2. **Agent with Knowledge Graph (KG-enhanced)**

Both agents interact with a simulated modular warehouse via MQTT.
The KG-enhanced agent uses an ontology to validate actions, reason about system state,
and log executed operations for future queries.

---

## Running Instructions

Follow the steps below to install the project and run the warehouse system and agent.

---

### 1. Installation

Clone the repository and enter it:

```bash
git clone https://github.com/MakaremHind/warehouse-agent-with-knowledge-graph
cd warehouse-agent-with-knowledge-graph
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

### 2. Start the MQTT Broker

The system requires a local MQTT broker.

Start Mosquitto:

```bash
mosquitto -v
```

The broker must be running on:

```
localhost:1883
```

---

### 3. Start the Warehouse System Scripts

Open **four separate terminals** (or tabs).
in each terminal and run the following scripts:

if you want to run agent with kg:
```bash
cd '.\Warehouse LLM Agent with KG\'
```

without kg:
```bash
cd '.\Warehouse LLM Agent without KG\'
```

**Terminal 1 — Master State Publisher**
(Reports the master controller status)
```bash
python .\scripts\mock_master_state.py
```

**Terminal 2 — Order Execution Simulator**
(Listens for transport orders and publishes success/failure responses)
```bash
python .\scripts\mock_order_executor.py
```

**Terminal 3 — Module & Topology Publisher**
(Publishes warehouse modules, poses, and connectivity)
```bash
python scripts/mock_module_publisher.py
```

**Terminal 4 — Warehouse Camera Simulator**
(Publishes detected boxes and fiducial markers)
```bash
python .\scripts\mock_camera_publisher.py
```

Wait until all scripts are running and MQTT messages start appearing in the logs.

---

### 4. Run the Agent

#### Run the KG-enhanced agent
```bash
cd '.\Warehouse LLM Agent with KG\'
python main.py
```

#### Run the baseline agent (without KG)
```bash
cd '.\Warehouse LLM Agent without KG\'
python main.py
```

---

## Example Commands

Once the agent is running, you can issue commands such as:

- move the red box from conveyor_02 to container_01
- plan a path between container_01 and conveyor_02
- confirm last order
- find last order
- diagnose failure

---

## Notes

- The **baseline agent** executes tool calls directly based on LLM reasoning.
- The **KG-enhanced agent** validates actions using ontology constraints before execution and logs all operations in the knowledge graph.
- The same warehouse simulation is used for both agents to ensure fair comparison.

---

## License

This project is intended for academic and research use.
