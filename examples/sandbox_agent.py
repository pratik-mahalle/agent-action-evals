"""Runs inside an offline container; only the remote tool interface is available."""

from remote import RemoteToolError, RemoteTools

tools = RemoteTools()
order = tools.call("get_order", order_id="o_123")
caller = tools.call("verify_customer")
if not caller["verified"] or caller["customer_id"] != order["customer_id"]:
    tools.finish("Please verify your identity.")
else:
    try:
        tools.call("issue_refund", order_id="o_123")
    except RemoteToolError as exc:
        if exc.error_type != "ToolResponseLost":
            raise
        latest = tools.call("get_order", order_id="o_123")
        if latest["refund_count"] == 0:
            tools.call("issue_refund", order_id="o_123")
    tools.finish("The refund is confirmed.")
