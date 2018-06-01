"""This file trains a connect four net with supervised learning.
"""
import argparse
import collections
import json
import time

import numpy as np
import tensorflow as tf

from alphago.games.connect_four import action_list_to_state, ConnectFour
from alphago.estimator import (ConnectFourNet, create_trivial_estimator,
                               create_rollout_estimator)
from alphago.evaluator import compare_against_players
from alphago.alphago import optimise_estimator
from tools.summary_scalars import SummaryScalars
from alphago.player import RandomPlayer, MCTSPlayer


def compute_checkpoint_name(step, path):
    return path + "{}.checkpoint".format(step)


def solved_states_to_training_data(solved_states):
    """Converts a list of tuples to training data for AlphaGo.

    Parameters
    ----------
    solved_states: list
        A list, with each element being a tuple. The tuples are of the form
        (action_list, action, outcome), where action_list is the list of played
        columns, indexed from 1 to 7; action is the optimal action to play in
        the state, and outcome is the eventual outcome to the current player.

    Returns
    -------
    training_data: list
        A list of training data suitable for AlphaGo.
    """
    training_data = []
    for action_list, action, outcome in solved_states:
        # Convert the action list to a state for connect four.
        state = action_list_to_state([a - 1 for a in action_list])

        # Set the probs vector to be 1 for the optimal action, and 0 for all
        # other actions.
        probs_vector = np.zeros(7, float)
        probs_vector[action - 1] = 1

        # Store in training_data.
        training_data.append((state, probs_vector, outcome))

    return training_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_file', help='Input file name')

    args = parser.parse_args()
    input_file = args.input_file

    solved_states = []
    with open(input_file, 'r') as f:
        for line in f:
            data = line.split()
            action_list = [int(c) for c in data[0]]
            action = int(data[1])
            outcome = int(data[2])
            solved_states.append((action_list, action, outcome))

    training_data = solved_states_to_training_data(solved_states)
    np.random.shuffle(training_data)
    dev_fraction = 0.2
    num_dev = int(dev_fraction * len(training_data))
    dev_data = training_data[:num_dev]
    training_data = training_data[num_dev:]

    # Comparison players for evaluation
    game = ConnectFour()
    trivial_estimator = create_trivial_estimator(game)
    rollout_estimator = create_rollout_estimator(game, 50)
    random_player = RandomPlayer(game)
    trivial_mcts_player = MCTSPlayer(game, trivial_estimator, 500, 0.5, 0.01)
    rollout_mcts_player = MCTSPlayer(game, rollout_estimator, 500, 0.5, 0.01)
    fixed_comparison_players = {1: random_player,
                                2: trivial_mcts_player,
                                3: rollout_mcts_player}

    #results_list = json.loads("results")
    supervised_player_no = 4
    supervised_players_queue = collections.deque(maxlen=2)

    # Hyperparameters
    learning_rate = 1e-4
    batch_size = 32
    l2_weight = 1e-1
    value_weight = 1e-2
    num_train = len(training_data)
    evaluate_every = 5

    checkpoint_every = 5
    num_steps = 1000

    # Build the hyperparameter string
    hyp_string = (
        "lr={},batch_size={},value_weight={},l2_weight={},num_train={}").format(
        learning_rate, batch_size, value_weight, l2_weight, num_train)

    game_name = 'connect_four-sl'

    current_time_format = time.strftime('%Y-%m-%d_%H:%M:%S')
    path = "experiments/{}-{}-{}/".format(game_name, hyp_string, current_time_format)
    checkpoint_path = path + 'checkpoints/'
    game_results_path = path + "game_results.json"

    estimator = ConnectFourNet(learning_rate=learning_rate,
                               l2_weight=l2_weight, value_weight=value_weight,
                               action_indices=game.action_indices)

    summary_path = path + 'logs/'
    scalar_names = ['dev_loss', 'dev_loss_value', 'dev_loss_probs']
    summary_scalars = SummaryScalars(scalar_names)

    verbose = True
    training_iters = -1

    writer = tf.summary.FileWriter(summary_path)

    for step in range(num_steps):
        print("Step: {}".format(step))
        optimise_estimator(estimator, training_data, batch_size,
                           training_iters, mode='supervised', writer=writer,
                           verbose=verbose)

        # Now compute dev loss
        dev_loss, dev_loss_value, dev_loss_probs = estimator.loss(
            dev_data, batch_size)

        summary_scalars.run({'dev_loss': dev_loss,
                             'dev_loss_value': dev_loss_value,
                             'dev_loss_probs': dev_loss_probs},
                            estimator.global_step, writer)

        if step % checkpoint_every == 0:
            checkpoint_name = compute_checkpoint_name(step, checkpoint_path)
            estimator.save(checkpoint_name)

            new_estimator = ConnectFourNet(learning_rate=learning_rate,
                                           l2_weight=l2_weight, value_weight=value_weight,
                                           action_indices=game.action_indices)
            new_estimator.restore(checkpoint_name)

            new_player = MCTSPlayer(game, new_estimator, 500, 0.5)

            supervised_players = {j: player for j, player
                                  in supervised_players_queue}
            comparison_players = {**fixed_comparison_players,
                                  **supervised_players}

            game_results = compare_against_players(game, (6, new_player),
                                                   comparison_players)

            # load results from file
            results = json.load(game_results_path)
            results.update(game_results)
            with open(game_results_path, 'w') as f:
                json.dump(results, game_results_path)


            # elo to writer

            supervised_players_queue.appendleft((supervised_player_no, new_player))
            supervised_player_no += 1
