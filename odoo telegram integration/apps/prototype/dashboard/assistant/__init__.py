"""The dashboard's AI assistant: an orchestrator that answers general questions itself and hands work
to two agents -- the data agent (read-only questions about the monitoring data) and the view agent
(hides / shows / moves dashboard sections). The model (OpenAI or Claude) is called only from assistant.llm."""
