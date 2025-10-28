import asyncio
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, List

import boto3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class HelloWorldBedrockAgent:
    def __init__(self):
        # Initialize session and client objects
        self.session = None
        self.exit_stack = AsyncExitStack()
        self.model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        self.bedrock_runtime = None

    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server"""
        if not server_script_path.endswith(".py"):
            raise ValueError("Server script must be a Python file with .py extension")

        print(f"Starting the MCP server: {server_script_path}...")

        # Use environment variables for server process
        env = os.environ.copy()

        # Start the server as a subprocess
        server_params = StdioServerParameters(
            command="python3", args=[server_script_path], env=env
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        # Initialize the session
        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        print(
            "\nConnected to server with tools:", [tool.name for tool in response.tools]
        )
        return response.tools

    async def initialize_bedrock(self):
        """Initialize the Amazon Bedrock client"""
        print("Initializing Amazon Bedrock client...")
        try:
            self.bedrock_runtime = boto3.client(
                "bedrock-runtime", region_name="us-west-2"
            )
            print(f"Using model: {self.model_id}")
            return True
        except Exception as e:
            print(f"Error initializing Bedrock client: {str(e)}")
            print(
                "Make sure you have the necessary AWS credentials and permissions set up."
            )
            return False

    def extract_tool_result(self, tool_result):
        """Extract content from a CallToolResult object or other result types"""
        try:
            # If it's a string, number, bool, etc., return it directly
            if isinstance(tool_result, (str, int, float, bool)):
                return tool_result

            # If it has a content attribute (like CallToolResult)
            if hasattr(tool_result, "content"):
                content = tool_result.content

                # If content is a list (like TextContent objects)
                if isinstance(content, list) and content:
                    # If the first item has a text attribute
                    if hasattr(content[0], "text"):
                        return content[0].text
                    # Otherwise return the list
                    return content

                # For other content types
                return str(content)

            # If it's already a dict or list, return as is
            if isinstance(tool_result, (dict, list)):
                return tool_result

            # Fallback to string representation
            return str(tool_result)

        except Exception as e:
            print(f"Error extracting tool result: {e}")
            return str(tool_result)

    async def process_query(self, query: str, available_tools: List[Any]):
        """Process a user query using Bedrock and the MCP tools"""
        if not self.bedrock_runtime:
            return "Bedrock client not initialized"

        # Format tools for Bedrock
        tool_list = [
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": tool.inputSchema},
                }
            }
            for tool in available_tools
        ]

        # Create the system message
        system_prompt = """You are a helpful assistant with access to calculator and greeting tools. 
When asked about calculations, use the calculator tools like add, subtract, multiply, and divide.
When asked to greet someone, use the greet tool.
When asked for a joke, use the tell_joke tool.
Always include the full text of any joke or greeting in your response to make sure the user can see it.
Respond in a friendly and helpful manner. Keep your answers brief but informative."""

        # Initialize messages array - exactly like in nova_act_mcp_client.py
        messages = [{"role": "user", "content": [{"text": query}]}]

        try:
            # Call Amazon Bedrock with the user query - exactly like in nova_act_mcp_client.py
            print("Sending query to Bedrock...")
            response = self.bedrock_runtime.converse(
                modelId=self.model_id,
                messages=messages,
                inferenceConfig={"temperature": 0.7},
                toolConfig={"tools": tool_list},
                system=[{"text": system_prompt}],
            )

            # Extract the assistant's response - exactly like in nova_act_mcp_client.py
            response_message = response["output"]["message"]
            final_responses = []
            tool_results = {}

            # Process each content block in the response - exactly like in nova_act_mcp_client.py
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
                    raw_tool_result = await self.session.call_tool(
                        tool_name, tool_input
                    )

                    # Extract the actual content from the tool result
                    extracted_result = self.extract_tool_result(raw_tool_result)
                    print(f"Raw tool result type: {type(raw_tool_result)}")
                    print(f"Extracted result: {extracted_result}")

                    # Save the result for later display
                    tool_results[tool_name] = extracted_result

                    # Create follow-up message with tool result
                    tool_result_message = {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "content": [{"json": {"result": extracted_result}}],
                                }
                            }
                        ],
                    }

                    # Add the AI message and tool result to messages
                    messages.append(response_message)
                    messages.append(tool_result_message)

                    # Make another call to get the final response
                    follow_up_response = self.bedrock_runtime.converse(
                        modelId=self.model_id,
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

            # Compose the final response with explicit tool results
            final_text = "\n".join(final_responses)

            # If we have tool results but they're not obviously included in the response,
            # add them explicitly
            for tool_name, result in tool_results.items():
                if tool_name == "tell_joke" and result not in final_text:
                    final_text += f"\n\nJoke: {result}"
                elif tool_name == "greet" and result not in final_text:
                    final_text += f"\n\nGreeting: {result}"
                elif (
                    tool_name in ["add", "subtract", "multiply", "divide"]
                    and str(result) not in final_text
                ):
                    final_text += f"\n\nCalculation result: {result}"

            return final_text

        except Exception as e:
            print(f"Error in Bedrock API call: {str(e)}")
            import traceback

            traceback.print_exc()
            return f"Error: {str(e)}"

    async def chat_loop(self, available_tools: List[Any]):
        """Run an interactive chat loop"""
        print("\nYou can now chat with the agent. Type 'exit' to quit.")

        while True:
            try:
                # Get user input
                user_query = input("\nYou: ")
                if user_query.lower() in ["exit", "quit"]:
                    break

                # Process the query
                response = await self.process_query(user_query, available_tools)
                print("\nAssistant:", response)

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()
        print("\nShutting down and cleaning up resources...")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python hello_world_mcp_client.py <path_to_server_script>")
        print("Example: python hello_world_mcp_client.py hello_world_mcp_server.py")
        sys.exit(1)

    server_script_path = sys.argv[1]
    agent = HelloWorldBedrockAgent()

    try:
        # Connect to the MCP server and get available tools
        available_tools = await agent.connect_to_server(server_script_path)

        # Initialize the Bedrock client
        if await agent.initialize_bedrock():
            # Run the chat loop
            await agent.chat_loop(available_tools)
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        # Clean up resources
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
