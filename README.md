# Warehouse Agent with Knowledge Graph

This repository contains two versions of an LLM-based warehouse control agent:

- **Agent without Knowledge Graph**: baseline ReAct-style agent using direct tool calls.
- **Agent with Knowledge Graph**: KG-grounded agent with ontology-based validation, reasoning, and persistent operational memory.

## Structure
- `agent_with_kg/` — ontology-aware agent with KG validation and logging
- `agent_without_kg/` — baseline agent without symbolic grounding
- `experiments/` — evaluation logs and result tables
- `docs/` — report figures and documentation

## Goal
Evaluate the impact of Knowledge Graph grounding on:
- execution correctness
- reasoning robustness
- failure prevention
- tool selection accuracy

This project accompanies a Master's internship on **Human-in-the-Loop Control of Modular Material-Handling Systems Using an LLM Agent**.
