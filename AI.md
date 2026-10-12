4. 大模型如何判断是否需要工具？
1）把工具清单和描述发给模型
2）System prompt 软引导
  软引导就是给大模型提供的自然语言的系统提示词；如：你是运营助手 Operations Copilot，服务公司内部的运营人员。你通过一组已注册的只读工具查询真实业务数据，然后用运营人员能直接看懂的话回答。
3）Agent SDK的agent loop驱动执行结果
  Agent SDK内封装了模型调用，工具注册，工具执行，事件流，多轮循环控制等；该项目把MCP工具和大模型客户端交给了Agent SDK,让它跑；
（1）模型若决定调用工具->输出tool call
    模型输出一次tool call → 对应地，SDK会执行一次MCP工具调用，也就是tool call是一次调用动作。
（2）SDK（扮演 MCP client 角色）通过 MCP 协议向 MCP server 服务发动调用请求，结果回传给模型
    通过MCP tool的每个都会有参数带描述 —— jsonschema tag 就是写给模型看的参数说明；
（3）模型再决定是继续调用下一个工具，还是输出最终文本回答
（4）max_turns兜底限制轮数，防止模型无限调用