# MCP Nova Act Examples

An MCP (Model Context Protocol) server implementation for Amazon Nova Act, allowing LLMs to control web browsers through a standardized MCP interface.

## Features

- Execute browser actions through natural language instructions
- Run parallel tasks across multiple browser instances
- Capture and store action results
- Take screenshots of browser sessions
- Save results to JSON files for later analysis

## Prerequisites

1. Operating System: MacOS or Ubuntu (Nova Act requirements)
2. Python 3.10 or above
3. A valid Nova Act API key (obtain from https://nova.amazon.com/act)
4. Amazon Bedrock access:
   - [Amazon Bedrock enabled in your AWS account](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html)
   - AWS credentials and region properly configured

## Installation

1. Install dependencies from main folder:
   ```bash
   pip install -r requirements.txt
   ```

2. Set your Nova Act API key as an environment variable:
   ```bash
   export NOVA_ACT_API_KEY="your_api_key"
   ```

3. Configure AWS credentials following the [AWS CLI Quickstart Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-quickstart.html)

## Usage

### Starting the Client and Server

```bash
python nova_act_mcp_client.py nova_act_mcp_server.py
```

This command will:
1. Start the MCP server that exposes Nova Act capabilities
2. Launch the MCP client that connects to the server
3. Enable communication between Claude and the Nova Act browser automation

## Claude Desktop Integration

For setting up and using this server with Claude Desktop, please follow the official [Claude Desktop MCP Setup Guide](https://modelcontextprotocol.io/quickstart/user).


## Troubleshooting

- If browser sessions fail to start, check your Nova Act API key
- For parallel execution issues, try reducing the number of concurrent tasks
- Browser performance problems may indicate insufficient system resources
- If actions are not working as expected, try making them more specific and explicit
- Might need to install chrome with this command: `playwright install chrome`
