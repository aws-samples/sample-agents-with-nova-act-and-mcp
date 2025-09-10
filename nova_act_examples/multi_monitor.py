import os
import time
import uuid
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from nova_act import NovaAct

# make logs directory
os.makedirs("./logs", exist_ok=True)


def parse_response(response, field_type):
    """Parse Nova Act response, handling both JSON and plain text formats"""
    if not response:
        return "N/A"
    
    # Try to parse as JSON first
    try:
        data = json.loads(response)
        if field_type == "rating" and "average_rating" in data:
            return f"{data['average_rating']} stars"
        elif field_type == "size" and "screen_size" in data:
            return f"{data['screen_size']} inches"
        elif field_type == "price" and "price" in data:
            return data["price"]
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Return as-is if it's already plain text
    return response


def check_monitor_on_amazon(monitor_model, headless=True):
    """Search for a specific monitor model on Amazon and extract information"""
    # Create a unique ID for this thread's browser session
    session_id = uuid.uuid4().hex[:8]
    print(f"[Thread {session_id}] Starting search for {monitor_model}")

    results = {"model": monitor_model, "price": "N/A", "rating": "N/A", "size": "N/A"}

    try:
        # Each thread gets its own isolated browser session
        with NovaAct(
            starting_page="https://www.amazon.com",
            record_video=True,
            headless=headless,
            logs_directory="./logs",
        ) as n:
            print(f"[Thread {session_id}] Browser session started")

            # Search for the specific monitor model
            n.act(f"search for '{monitor_model}'")

            # Click on the first result
            n.act("click on the first search result")

            # Get the price
            price_result = n.act("What is the current price of this monitor? Respond with just the price like '$369.99'")
            results["price"] = parse_response(price_result.response, "price") or "N/A"

            # Get the rating
            rating_result = n.act("What is the average rating of this monitor? Respond with just the number and 'stars', like '4.5 stars'")
            results["rating"] = parse_response(rating_result.response, "rating") or "N/A"

            # Get screen size
            size_result = n.act("What is the screen size of this monitor? Respond with just the size and 'inches', like '27 inches'")
            results["size"] = parse_response(size_result.response, "size") or "N/A"

        print(f"[Thread {session_id}] ✅ Completed search for {monitor_model}")
    except Exception as e:
        print(f"[Thread {session_id}] ❌ Error searching for {monitor_model}: {e}")

    return results


def parallel_monitor_comparison():
    # List of popular monitor models to check
    monitor_models = [
        "Dell S2722QC 27-inch 4K USB-C Monitor",
        "LG 27GP850-B 27-inch Ultragear Gaming Monitor",
        "Samsung Odyssey G7 32-inch Gaming Monitor",
    ]

    all_results = []
    start_time = time.time()

    # On machines with fewer cores, running too many browser instances can cause failures
    max_workers = min(3, len(monitor_models))
    print(f"Starting parallel execution with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit search tasks with a delay between submissions
        future_to_model = {}
        for model in monitor_models:
            # Submit the task
            future = executor.submit(check_monitor_on_amazon, model, True)
            future_to_model[future] = model

            # Add a delay before submitting the next task to avoid resource spikes
            time.sleep(5)  # Give 5 seconds between browser startups

        # Process results as they complete
        for future in as_completed(future_to_model):
            model = future_to_model[future]
            try:
                results = future.result()
                if results:  # Make sure results isn't None
                    all_results.append(results)
            except Exception as e:
                print(f"❌ Error processing results for {model}: {e}")

    elapsed = time.time() - start_time
    print(f"Parallel execution completed in {elapsed:.2f} seconds")

    # Print comparison table
    print("\nMonitor Comparison:")
    print("-" * 80)
    print(f"{'Model':<30} {'Price':<10} {'Rating':<10} {'Size':<10}")
    print("-" * 80)
    
    for result in all_results:
        # Ensure all values are strings to prevent formatting errors
        model = str(result.get('model', 'Unknown'))[:28] if result.get('model') is not None else 'Unknown'
        price = str(result.get('price', 'N/A')) if result.get('price') is not None else 'N/A'
        rating = str(result.get('rating', 'N/A')) if result.get('rating') is not None else 'N/A'
        size = str(result.get('size', 'N/A')) if result.get('size') is not None else 'N/A'
        
        print(f"{model:<30} {price:<10} {rating:<10} {size:<10}")

    return all_results


if __name__ == "__main__":
    parallel_monitor_comparison()
