import asyncio
import json
import os
from contextlib import AsyncExitStack
from typing import Optional

import boto3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NOVA_ACT_API_KEY = os.getenv("NOVA_ACT_API_KEY")

bedrock_runtime = boto3.client(
    service_name="bedrock-runtime",
    region_name="us-west-2",
)


class NovaActMCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server"""
        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python3" if is_python else "node"

        # Set environment variables including the API key
        env = os.environ.copy()
        if NOVA_ACT_API_KEY:
            env["NOVA_ACT_API_KEY"] = NOVA_ACT_API_KEY
        else:
            print("Warning: NOVA_ACT_API_KEY environment variable not set")
            raise ValueError("NOVA_ACT_API_KEY environment variable not set")

        server_params = StdioServerParameters(
            command=command, args=[server_script_path], env=env
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    async def process_query(self, query: str) -> str:
        """Process a query using Bedrock and available tools"""
        # Get available tools
        response = await self.session.list_tools()
        if not response.tools:
            return "No tools available on the server."

        available_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]

        # Prepare messages and tools for Bedrock
        messages = [{"role": "user", "content": [{"text": query}]}]

        # Format tools for Bedrock
        tool_list = [
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": {"json": tool["input_schema"]},
                }
            }
            for tool in available_tools
        ]

        # Set system prompt
        system_prompt = "You are an AI assistant capable of using tools to help users. Use the provided tools when necessary."

        # Generate conversation with Bedrock
        model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        try:
            # Make the API call to Bedrock
            response = bedrock_runtime.converse(
                modelId=model_id,
                messages=messages,
                inferenceConfig={"temperature": 0.7},
                toolConfig={"tools": tool_list},
                system=[{"text": system_prompt}],
            )

            # Process the response
            final_responses = []
            response_message = response["output"]["message"]

            # Process each content block in the response
            for content_block in response_message["content"]:
                if "text" in content_block:
                    # Add text responses to our final output
                    final_responses.append(content_block["text"])

                elif "toolUse" in content_block:
                    # Handle tool usage
                    tool_use = content_block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_input = tool_use["input"]
                    tool_use_id = tool_use["toolUseId"]

                    print(f"Calling tool: {tool_name} with input: {tool_input}")
                    final_responses.append(f"[Calling tool {tool_name}]")

                    # Call the tool through MCP session
                    tool_result = await self.session.call_tool(tool_name, tool_input)

                    # Extract the content from the tool result and convert to JSON if needed
                    try:
                        # Check what we actually got back
                        # print(f"Raw tool result type: {type(tool_result)}")
                        # print(f"Raw tool result: {tool_result}")

                        # Convert the content to a proper JSON object if it's a string
                        if hasattr(tool_result, "content"):
                            content_val = tool_result.content
                            print(f"Content type: {type(content_val)}")

                            # Handle different types of content
                            if isinstance(content_val, list):
                                # Handle list of TextContent objects
                                if len(content_val) > 0 and hasattr(
                                    content_val[0], "text"
                                ):
                                    # Extract text from the first TextContent object
                                    text_content = content_val[0].text
                                    try:
                                        # Try to parse as JSON
                                        result_content = json.loads(text_content)
                                    except json.JSONDecodeError:
                                        # If not valid JSON, use as text
                                        result_content = {"text": text_content}
                                else:
                                    # Just a list of normal values
                                    result_content = {"items": content_val}
                            elif isinstance(content_val, str):
                                try:
                                    # First try to parse as JSON
                                    result_content = json.loads(content_val)
                                except json.JSONDecodeError:
                                    # If not JSON, use as plain string
                                    result_content = {"text": content_val}
                            else:
                                # If it's already a dict/list, use directly
                                result_content = content_val
                        else:
                            # If no content attribute, convert the whole object to string
                            result_content = {"text": str(tool_result)}
                    except Exception as e:
                        print(f"Error processing tool result: {e}")
                        result_content = {"error": str(e)}

                    # print(f"Result content type: {type(result_content)}")
                    # print(f"Result content: {result_content}")

                    # Ensure we have a properly structured result for Bedrock
                    # Bedrock expects a simple JSON object, not a nested structure
                    if (
                        isinstance(result_content, dict)
                        and "starting_page" in result_content
                    ):
                        # For browser_session results, extract the most useful info

                        # Check if we have a response in the final result
                        final_result = result_content.get("final_result", {})
                        response_text = final_result.get("response")
                        final_page = final_result.get("final_page")

                        if result_content.get("collected_data"):
                            result_for_bedrock = {
                                "result": result_content.get("collected_data")
                            }
                        elif response_text:
                            # If we have a text response from the tool, use it
                            result_for_bedrock = {
                                "result": response_text,
                                "url": final_page,
                                "action": final_result.get("action", ""),
                            }
                        elif final_page:
                            result_for_bedrock = {
                                "result": f"Successfully loaded page: {final_page}",
                                "url": final_page,
                            }
                        else:
                            result_for_bedrock = {
                                "result": f"Opened {result_content.get('starting_page', 'page')}",
                            }

                        # Include all results for context if available
                        if result_content.get("all_results"):
                            all_results = []
                            for res in result_content.get("all_results", []):
                                if res.get("response"):
                                    all_results.append(
                                        {
                                            "action": res.get("action", ""),
                                            "response": res.get("response", ""),
                                        }
                                    )
                            if all_results:
                                result_for_bedrock["all_results"] = all_results
                    else:
                        # For other results, use as is
                        result_for_bedrock = {"result": result_content}

                    # Create follow-up message with tool result
                    tool_result_message = {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "content": [{"json": result_for_bedrock}],
                                }
                            }
                        ],
                    }

                    # Add the AI message and tool result to messages
                    messages.append(response_message)
                    messages.append(tool_result_message)

                    # Make another call to get the final response
                    follow_up_response = bedrock_runtime.converse(
                        modelId=model_id,
                        messages=messages,
                        inferenceConfig={"temperature": 0.7},
                        toolConfig={"tools": tool_list},
                        system=[{"text": system_prompt}],
                    )

                    # Add the follow-up response to our final output
                    follow_up_text = follow_up_response["output"]["message"]["content"][
                        0
                    ]["text"]
                    final_responses.append(follow_up_text)

            return "\n".join(final_responses)

        except Exception as e:
            print(f"Error in Bedrock API call: {e}")
            return f"Error: {str(e)}"

    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nHello World MCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == "quit":
                    break

                response = await self.process_query(query)
                print("\n" + response)

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python test_client.py <path_to_server_script>")
        sys.exit(1)

    client = NovaActMCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import sys

    asyncio.run(main())
