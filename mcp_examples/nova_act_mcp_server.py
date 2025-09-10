#!/usr/bin/env python

import asyncio
import json
import multiprocessing
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from nova_act import ActError, NovaAct

# Initialize FastMCP server
mcp = FastMCP("nova-act-server")

# Global variables for session and results
nova_act_instance = None
results_store = {}
session_lock = threading.Lock()
results_lock = threading.Lock()


# Helper functions
def generate_id(prefix: str) -> str:
    """Generate a unique ID for results"""
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def save_results_to_file(
    file_path: str, result_ids: Optional[List[str]] = None
) -> bool:
    """Save selected results to a JSON file"""
    with results_lock:
        if result_ids:
            data = {
                rid: results_store[rid] for rid in result_ids if rid in results_store
            }
        else:
            data = dict(results_store)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        return False


def execute_nova_act_task(task_args, result_file=None):
    """
    Execute a single Nova Act task in an isolated process.
    This function runs in a separate process for each task.
    """
    starting_page = task_args.get("starting_page")
    actions_input = task_args.get("actions", [])
    headless = task_args.get("headless", False)
    
    # Create log directory if it doesn't exist
    os.makedirs("./log", exist_ok=True)

    # Convert action strings to action objects if needed
    actions = []
    for action in actions_input:
        if isinstance(action, str):
            # Convert string to action object
            actions.append({"action": action})
        else:
            # Already an action object
            actions.append(action)

    task_results = []

    try:
        # Create and start NovaAct instance
        with NovaAct(
            starting_page=starting_page,
            headless=headless,
            record_video=True,
            logs_directory="./log",
        ) as nova_act:
            # Execute each action in sequence
            for action_params in actions:
                action_text = action_params.get("action")
                schema = action_params.get("schema")
                max_steps = action_params.get("max_steps")

                kwargs = {}
                if schema:
                    kwargs["schema"] = schema
                if max_steps:
                    kwargs["max_steps"] = max_steps

                try:
                    result = nova_act.act(action_text, **kwargs)

                    # Create a result object
                    result_id = generate_id("result")
                    result_data = {
                        "result_id": result_id,
                        "action": action_text,
                        "starting_page": starting_page,
                        "final_page": nova_act.page.url,
                        "response": result.response,
                        "parsed_response": (
                            result.parsed_response
                            if hasattr(result, "parsed_response")
                            else None
                        ),
                        "valid_json": (
                            result.valid_json if hasattr(result, "valid_json") else None
                        ),
                        "matches_schema": (
                            result.matches_schema
                            if hasattr(result, "matches_schema")
                            else None
                        ),
                        "metadata": (
                            {
                                "num_steps_executed": result.metadata.num_steps_executed,
                                "start_time": str(result.metadata.start_time),
                                "end_time": str(result.metadata.end_time),
                                "prompt": str(result.metadata.prompt),
                            }
                            if hasattr(result, "metadata")
                            else {}
                        ),
                    }

                    task_results.append(result_data)
                except Exception as e:
                    task_results.append({"action": action_text, "error": str(e)})

        # Write results to file if specified
        if result_file:
            with open(result_file, "w") as f:
                json.dump({"starting_page": starting_page, "results": task_results}, f)

        return {"starting_page": starting_page, "results": task_results}
    except Exception as e:
        result = {
            "starting_page": starting_page,
            "error": str(e),
            "results": task_results,
        }
        if result_file:
            with open(result_file, "w") as f:
                json.dump(result, f)
        return result


# MCP tools
@mcp.tool()
async def browser_session(
    starting_page: str,
    actions: List[str],
    headless: bool = False,
) -> Dict[str, Any]:
    """Start a browser and perform a sequence of actions in a single command.

        USE THIS TOOL for tasks that need to be executed in sequence within the same browser session, where each action depends on the state created by previous actions.

        For multiple independent tasks that can run in parallel, use execute_parallel_browser_tasks instead.

    When writing actions for Nova Act:

    1. Be prescriptive and succinct - tell the agent exactly what to do
       ✅ "Click the hamburger menu icon, go to Order History"
       ❌ "Find my order history"

    2. Break complex tasks into smaller actions
       ✅ "Search for hotels in Houston", then "Sort by avg customer review"
       ❌ "Find the highest rated hotel in Houston"

    3. Be specific about UI elements and navigation patterns
       ✅ "Scroll down until you see 'add to cart' and then click it"
       ❌ "Add the item to cart"

    4. For searches, be explicit about interaction patterns
       ✅ "Type 'coffee maker' in the search box and press enter"
       ❌ "Search for coffee makers"

    5. For date selection, use absolute dates
       ✅ "Select dates March 23 to March 28"
       ❌ "Book for next week"

    6. For extracting information, use a dedicated action
       ✅ "Return a list of all visible product names and prices"
       ❌ "Find the cheapest option and tell me about it"

    Important limitations:
    - Nova Act cannot interact with elements hidden behind mouseovers
    - Nova Act cannot interact with browser windows/modals
    - Nova Act works best with short, specific instructions

        This function returns a structured response with:
        - starting_page: The URL where the browser started
        - collected_data: The parsed response data
    """
    global nova_act_instance

    # Close any existing browser session
    with session_lock:
        if nova_act_instance:
            try:
                nova_act_instance.stop()
            except:
                raise ActError("Failed to stop existing browser session")

    # Start a new NovaAct session in a separate thread
    def run_browser_session():
        try:
            # Convert simple action strings to action objects
            action_objects = [{"action": action_text} for action_text in actions]

            # Create and use a NovaAct instance with context manager
            results = []
            final_response = {}

            # Create log directory if it doesn't exist
            os.makedirs("./log", exist_ok=True)
            
            # Create NovaAct instance without context manager to keep it alive
            nova_act = NovaAct(
                starting_page=starting_page,
                headless=headless,
                record_video=True,
                logs_directory="./log",
            )
            nova_act.start()
            
            # Keep a reference to the nova_act instance
            with session_lock:
                global nova_act_instance
                nova_act_instance = nova_act

                # Execute each action in sequence
                for i, action_text in enumerate(actions):
                    try:
                        result = nova_act.act(action_text)

                        # Store the result
                        result_id = generate_id("result")
                        result_data = {
                            "result_id": result_id,
                            "action": action_text,
                            "starting_page": starting_page,
                            "final_page": nova_act.page.url,
                            "response": result.response,
                            "parsed_response": (
                                result.parsed_response
                                if hasattr(result, "parsed_response")
                                else None
                            ),
                            "valid_json": (
                                result.valid_json
                                if hasattr(result, "valid_json")
                                else None
                            ),
                            "matches_schema": (
                                result.matches_schema
                                if hasattr(result, "matches_schema")
                                else None
                            ),
                            "metadata": (
                                {
                                    "num_steps_executed": result.metadata.num_steps_executed,
                                    "start_time": str(result.metadata.start_time),
                                    "end_time": str(result.metadata.end_time),
                                    "prompt": str(result.metadata.prompt),
                                }
                                if hasattr(result, "metadata")
                                else {}
                            ),
                        }

                        with results_lock:
                            results_store[result_id] = result_data

                        # Store action result
                        result_item = {
                            "result_id": result_id,
                            "action": action_text,
                            "starting_page": starting_page,
                            "final_page": nova_act.page.url,
                            "response": result.response,
                            "parsed_response": (
                                result.parsed_response
                                if hasattr(result, "parsed_response")
                                else None
                            ),
                            "valid_json": (
                                result.valid_json
                                if hasattr(result, "valid_json")
                                else None
                            ),
                            "matches_schema": (
                                result.matches_schema
                                if hasattr(result, "matches_schema")
                                else None
                            ),
                        }

                        results.append(result_item)

                        # If this is the last action, it usually contains the data we want
                        # Store it in final_response for easier access
                        if i == len(actions) - 1:
                            final_response = result_item

                    except Exception as e:
                        error_result = {"action": action_text, "error": str(e)}
                        results.append(error_result)

                        # If this was the last action, store the error in final_response
                        if i == len(actions) - 1:
                            final_response = error_result

            # Keep the browser session alive for further interaction

            # Return a structured response with both complete results and the final response
            return {
                "starting_page": starting_page,
                "all_results": results,
                "final_result": final_response,
                "collected_data": final_response.get(
                    "parsed_response", final_response.get("response", None)
                ),
            }
        except Exception as e:
            with session_lock:
                nova_act_instance = None
            return {"error": str(e)}

    return await asyncio.to_thread(run_browser_session)


@mcp.tool()
async def browser_action(
    action: str,
    schema: Optional[Dict[str, Any]] = None,
    max_steps: Optional[int] = None,
) -> Dict[str, Any]:
    """Perform a single action in the Nova Act browser"""
    global nova_act_instance

    with session_lock:
        if not nova_act_instance:
            raise ValueError("Browser session not started. Use browser_session first.")
        act = nova_act_instance

    # Execute the action in a separate thread
    def execute_action():
        try:
            kwargs = {}
            if schema:
                kwargs["schema"] = schema
            if max_steps:
                kwargs["max_steps"] = max_steps

            result = act.act(action, **kwargs)

            # Store the result
            result_id = generate_id("result")
            result_data = {
                "result_id": result_id,
                "action": action,
                "final_page": act.page.url,
                "response": result.response,
                "parsed_response": (
                    result.parsed_response
                    if hasattr(result, "parsed_response")
                    else None
                ),
                "valid_json": (
                    result.valid_json if hasattr(result, "valid_json") else None
                ),
                "matches_schema": (
                    result.matches_schema if hasattr(result, "matches_schema") else None
                ),
                "metadata": (
                    {
                        "num_steps_executed": result.metadata.num_steps_executed,
                        "start_time": result.metadata.start_time,
                        "end_time": result.metadata.end_time,
                        "prompt": result.metadata.prompt,
                    }
                    if hasattr(result, "metadata")
                    else {}
                ),
            }

            with results_lock:
                results_store[result_id] = result_data

            return {
                "result_id": result_id,
                "final_page": act.page.url,
                "response": result.response,
                "parsed_response": (
                    result.parsed_response
                    if hasattr(result, "parsed_response")
                    else None
                ),
                "valid_json": (
                    result.valid_json if hasattr(result, "valid_json") else None
                ),
                "matches_schema": (
                    result.matches_schema if hasattr(result, "matches_schema") else None
                ),
            }
        except Exception as e:
            print(f"Error executing action: {e}")
            raise

    return await asyncio.to_thread(execute_action)


@mcp.tool()
async def execute_parallel_browser_tasks(
    browser_tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Execute multiple sequences of actions in parallel across different browser sessions.

    WHEN TO USE:
    - For tasks that don't depend on each other's results
    - When you need to collect information from multiple sources simultaneously
    - When you want to significantly speed up web workflows through parallelization

    EXAMPLES:
    - Comparing multiple products on Amazon (each product in a separate browser)
    - Searching for information across different websites
    - Checking apartment listings and calculating distances to locations (as shown in the documentation)

    Each browser_task should include:
    - starting_page: URL to start the browser
    - actions: List of actions to perform, following these guidelines:
      1. Make each action prescriptive and succinct
         ✅ "Click the hamburger menu icon"
         ❌ "Find the menu"

      2. Be specific about UI elements
         ✅ "Scroll down until you see 'Reviews' and click it"
         ❌ "Go to the reviews section"

      3. For data extraction, use a dedicated action with schema
         ✅ {"action": "Return the current price and rating", "schema": {...}}
         ❌ "Tell me about this product"

    Format options for actions:
    1. Simple string format: ["Search for iPhone", "Click on the first result"]
    2. Object format for data extraction: [{"action": "Return product details", "schema": {...}}]

    Browser configuration options:
    - headless: Run browsers without visible UI (default: False)
    - user_data_dir: Path to browser profile (note: each session requires its own)
    - quiet: Suppress logs (default: False)

    IMPORTANT NOTES:
    - Each task runs in its own isolated browser - they cannot interact with each other
    - For authentication, each session needs its own user_data_dir
    - Nova Act cannot interact with elements hidden behind mouseovers
    - Data extraction works best with clear schemas

        Returns a list of task results, each containing:
        - starting_page: The URL where the browser started
        - final_result: The result of the last action (usually the most relevant)
    """

    # Create a temporary directory for result files
    temp_dir = tempfile.mkdtemp(prefix="nova_act_results_")

    # Prepare processes for each task
    processes = []
    result_files = []

    for i, task in enumerate(browser_tasks):
        # Create a file to store the result
        result_file = os.path.join(temp_dir, f"task_{i}_result.json")
        result_files.append(result_file)

        # Create and start a new process for this task
        process = multiprocessing.Process(
            target=execute_nova_act_task, args=(task, result_file)
        )
        processes.append(process)
        process.start()

    # Wait for all processes to complete
    for process in processes:
        process.join()

    # Collect results
    all_results = []

    for result_file in result_files:
        try:
            if os.path.exists(result_file):
                with open(result_file, "r") as f:
                    task_result = json.load(f)

                # Process the task result to add final_result and collected_data
                if "results" in task_result and task_result["results"]:
                    # Get the last result (final action)
                    final_result = task_result["results"][-1]

                    # Add final_result to the task_result
                    task_result["final_result"] = final_result

                    # Add collected_data extracted from the final_result
                    if "parsed_response" in final_result:
                        task_result["collected_data"] = final_result["parsed_response"]
                    elif "response" in final_result:
                        task_result["collected_data"] = final_result["response"]

                all_results.append(task_result)

                # Store results in the results_store
                if "results" in task_result:
                    with results_lock:
                        for result in task_result["results"]:
                            if "result_id" in result:
                                results_store[result["result_id"]] = result
            else:
                all_results.append({"error": "Result file not found"})

        except Exception as e:
            all_results.append({"error": str(e)})

    # Clean up temporary files
    try:
        for file in result_files:
            if os.path.exists(file):
                os.remove(file)
        os.rmdir(temp_dir)
    except Exception as e:
        raise ValueError(f"Error cleaning up temporary files: {e}")

    return all_results


@mcp.tool()
async def list_results() -> List[Dict[str, Any]]:
    """List all stored results"""
    result_infos = []
    with results_lock:
        for result_id, result_data in results_store.items():
            result_infos.append(
                {
                    "result_id": result_id,
                    "action": result_data.get("action", ""),
                    "response": result_data.get("response", ""),
                }
            )
    return result_infos


@mcp.tool()
async def get_result(result_id: str) -> Dict[str, Any]:
    """Get a specific result by ID"""
    with results_lock:
        if result_id not in results_store:
            raise ValueError(f"Result {result_id} not found")
        return results_store[result_id]


@mcp.tool()
async def save_results(file_path: str, result_ids: Optional[List[str]] = None) -> bool:
    """Save results to a file"""
    return save_results_to_file(file_path, result_ids)


@mcp.tool()
async def take_screenshot(save_path: Optional[str] = None, close_session: bool = True) -> str:
    """Take a screenshot in the browser session and optionally close it"""
    global nova_act_instance

    with session_lock:
        if not nova_act_instance:
            raise ValueError("Browser session not started. Use browser_session first.")
        act = nova_act_instance

    # Take screenshot in a separate thread
    def capture_screenshot():
        try:
            screenshot_bytes = act.page.screenshot()
            
            # Close session if requested
            if close_session:
                try:
                    act.stop()
                    with session_lock:
                        global nova_act_instance
                        nova_act_instance = None
                except:
                    pass  # Ignore errors during cleanup
            
            if save_path:
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(screenshot_bytes)
                return save_path
            else:
                import tempfile

                temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temp_file.write(screenshot_bytes)
                temp_file.close()
                return temp_file.name
        except Exception as e:
            raise

    return await asyncio.to_thread(capture_screenshot)


@mcp.tool()
async def close_browser() -> bool:
    """Close the browser session"""
    global nova_act_instance

    with session_lock:
        if not nova_act_instance:
            return False

        act = nova_act_instance

    # Stop NovaAct in a separate thread
    def stop_session(act_instance):
        try:
            act_instance.stop()
            return True
        except Exception as e:
            return False

    success = await asyncio.to_thread(stop_session, act)

    if success:
        with session_lock:
            nova_act_instance = None
        return True
    else:
        return False


# Run the server when the script is executed directly
if __name__ == "__main__":
    # Register multiprocessing start method
    multiprocessing.set_start_method("spawn", force=True)

    # Ensure clean shutdown on exit
    import atexit

    def cleanup():
        # Close the browser session if it exists
        global nova_act_instance
        if nova_act_instance:
            try:
                nova_act_instance.stop()
            except:
                pass  # Ignore errors during cleanup

    atexit.register(cleanup)

    # Start the server
    mcp.run()
