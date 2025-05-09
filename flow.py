from pocketflow import AsyncFlow, AsyncParallelBatchFlow
from nodes import DecisionNode

# --- Sequential Flow ---
def create_character_decision_flow() -> AsyncFlow:
    """Creates a reusable AsyncFlow containing a single Async DecisionNode.
    The character name must be set via flow parameters before running.
    """
    decision_node = DecisionNode(max_retries=3, wait=1) # Configure retries/wait
    # The flow consists of only this single node
    return AsyncFlow(start=decision_node)

# --- Parallel Flow ---
class ParallelCharacterDecisionFlow(AsyncParallelBatchFlow):
    """
    An AsyncParallelBatchFlow that runs the DecisionNode concurrently for multiple characters.
    """
    async def prep_async(self, shared: dict) -> list[dict]:
        """
        Determines which characters need to act in the current phase and returns
        a list of parameter dictionaries, one for each character.
        Expects 'acting_characters' list in the shared state.
        """
        acting_characters = shared.get("acting_characters")
        if not acting_characters:
            print("Warning: 'acting_characters' not found or empty in shared state for ParallelCharacterDecisionFlow prep.")
            return [] # Return empty list if no one is acting

        # Create a list of parameter dictionaries for the batch flow
        params_list = [{"character_name": name} for name in acting_characters]
        print(f"Parallel flow prepared for characters: {acting_characters}") # Debug print
        return params_list

    # No exec_async or post_async needed for the BatchFlow itself,
    # as it delegates execution to its start node (DecisionNode) for each param set.

def create_parallel_decision_flow() -> AsyncParallelBatchFlow:
    """
    Creates and returns the parallel decision flow.
    """
    # Instantiate the node that will be run in parallel for each character
    # Configure retries and wait time for the individual node runs
    decision_node = DecisionNode(max_retries=3, wait=1)

    # Create the parallel batch flow, starting with the decision node
    parallel_flow = ParallelCharacterDecisionFlow(start=decision_node)
    return parallel_flow

# Example Conceptual Usage:
# async def run_sequential_turn(shared_state, character_name):
#     flow = create_sequential_decision_flow()
#     flow.set_params({"character_name": character_name})
#     await flow.run_async(shared_state)
#
# async def run_parallel_phase(shared_state, acting_characters_list):
#     shared_state["acting_characters"] = acting_characters_list
#     flow = create_parallel_decision_flow()
#     await flow.run_async(shared_state)