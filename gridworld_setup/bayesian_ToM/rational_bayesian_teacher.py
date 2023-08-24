from minigrid.core.constants import DIR_TO_VEC
from minigrid.core.actions import Actions
import numpy as np
from queue import SimpleQueue
from typing import Callable


from environment import MultiGoalsEnv, MultiRoomsGoalsEnv
from utils import *
from .bayesian_teacher import BayesianTeacher


##
# Bayesian teacher that knows rational learner
##


class RationalBayesianTeacher(BayesianTeacher):
    def __init__(
        self,
        env: MultiGoalsEnv | MultiRoomsGoalsEnv,
        num_colors: int = 4,
        rf_values: np.ndarray = np.array([3, 5, 7]),
        Na: int = 6,
        lambd: float = 0.5,
        add_full_obs: bool = True,
    ) -> None:
        self.Na = Na
        self.rf_values_basic = rf_values
        self.add_full_obs = add_full_obs

        # Boltzmann temperature parameter (confidence in greedy)
        self.lambd = lambd

        self.num_colors = num_colors
        self.num_rf = len(rf_values) + 1 if self.add_full_obs else len(rf_values)

        # Init beliefs on the type of learner
        self.beliefs = (
            1.0 / (num_colors * len(rf_values)) * np.ones((num_colors, self.num_rf))
        )
        # Init env and learner beliefs about the env
        self.init_env(env)

        self.LOG = []

    def init_env(self, env: MultiGoalsEnv | MultiRoomsGoalsEnv) -> None:
        self.env = env
        self.gridsize = self.env.height
        if self.add_full_obs:
            self.rf_values = np.concatenate(
                (self.rf_values_basic, np.array([self.gridsize]))
            )
        else:
            self.rf_values = self.rf_values_basic

        self.learner_beliefs = (
            1.0
            / (2 + 2 * self.num_colors)
            * np.ones(
                (self.num_rf, self.gridsize, self.gridsize, 2 + 2 * self.num_colors)
            )
        )

        self.learner_going_to_subgoal = np.zeros(
            (self.num_colors, self.num_rf), dtype=bool
        )
        self.learner_going_to_goal = np.zeros(
            (self.num_colors, self.num_rf), dtype=bool
        )
        self.learner_reached_subgoal = np.zeros(
            (self.num_colors, self.num_rf), dtype=bool
        )
        self.learner_step_count = -1

        self.distance_subgoal = np.zeros(
            (self.num_rf, self.num_colors, self.gridsize, self.gridsize)
        )
        self.distance_goal = np.zeros(
            (self.num_rf, self.num_colors, self.gridsize, self.gridsize)
        )

    def update_learner_belief(self, rf_idx: int) -> None:
        receptive_field = self.rf_values[rf_idx]
        obs, vis_mask = compute_learner_obs(
            self.learner_pos, self.learner_dir, receptive_field, self.env
        )

        f_vec = DIR_TO_VEC[self.learner_dir]
        dir_vec = DIR_TO_VEC[self.learner_dir]
        dx, dy = dir_vec
        r_vec = np.array((-dy, dx))
        top_left = (
            self.learner_pos
            + f_vec * (receptive_field - 1)
            - r_vec * (receptive_field // 2)
        )
        new_cells = 0

        # For each cell in the visibility mask
        for vis_j in range(0, receptive_field):
            for vis_i in range(0, receptive_field):
                if not vis_mask[vis_i, vis_j]:
                    continue

                # Compute the world coordinates of this cell
                abs_i, abs_j = top_left - (f_vec * vis_j) + (r_vec * vis_i)
                if abs_i < 0 or abs_i >= self.gridsize:
                    continue
                if abs_j < 0 or abs_j >= self.gridsize:
                    continue

                one_hot = np.zeros(2 + 2 * self.num_colors)
                color_idx = obs[vis_i, vis_j, 1]

                if self.learner_pos == (abs_i, abs_j):
                    one_hot[0] = 1
                # Goal
                elif obs[vis_i, vis_j, 0] == 4:
                    one_hot[2 + (color_idx - 1) * 2] = 1
                # Subgoal (key)
                elif obs[vis_i, vis_j, 0] == 5:
                    one_hot[2 + (color_idx - 1) * 2 + 1] = 1
                # Wall
                elif obs[vis_i, vis_j, 0] == 2:
                    one_hot[1] = 1
                # Nothing
                else:
                    one_hot[0] = 1

                if np.any(self.learner_beliefs[rf_idx, abs_i, abs_j, :] != one_hot):
                    new_cells += 1

                self.learner_beliefs[rf_idx, abs_i, abs_j, :] = one_hot

        if new_cells > 0:
            for goal_color in range(self.num_colors):
                # Additional info --> update distance map
                if (
                    self.learner_going_to_subgoal[goal_color, rf_idx]
                    and not self.learner_reached_subgoal[goal_color, rf_idx]
                ):
                    self.LOG.append("Recompute distances to subgoal")
                    self.update_distance_subgoal(goal_color, rf_idx)

                elif self.learner_going_to_goal[goal_color, rf_idx]:
                    self.LOG.append("Recompute distances to goal")
                    self.update_distance_goal(goal_color, rf_idx)

    def learner_exploration_policy(self, goal_color: int, rf_idx: int) -> np.ndarray:
        prob_dist = np.zeros(self.Na)
        prob_dist[0] = 1
        prob_dist[1] = 1

        next_pos = self.learner_pos + DIR_TO_VEC[self.learner_dir]
        one_hot_empty = np.zeros(2 + self.num_colors * 2)
        one_hot_empty[0] = 1
        one_hot_subgoal = np.zeros(2 + self.num_colors * 2)
        one_hot_subgoal[2 + 2 * goal_color + 1] = 1
        if np.all(
            self.learner_beliefs[rf_idx, next_pos[0], next_pos[1], :] == one_hot_empty
        ) or np.all(
            self.learner_beliefs[rf_idx, next_pos[0], next_pos[1], :] == one_hot_subgoal
        ):  # No obstacle in front
            prob_dist[2] = 1

        prob_dist /= prob_dist.sum()
        return prob_dist

    def learner_greedy_policy(
        self, obj: str, rf_idx: int, goal_color: int
    ) -> np.ndarray:
        if obj == "goal":
            distance_map = self.distance_goal
        elif obj == "subgoal":
            distance_map = self.distance_subgoal
        else:
            raise ValueError("Unknown object for distance map")

        proba_dist = np.zeros(self.Na)
        const = distance_map[
            rf_idx, goal_color, self.learner_pos[0], self.learner_pos[1]
        ]
        for action in range(3):
            # Boltzman wrt distance to the goal
            if action in [0, 1]:  # Turn left or right
                if action == 0:
                    next_dir = (self.learner_dir - 1) % 4
                    next_next_dir = (self.learner_dir - 2) % 4
                elif action == 1:
                    next_dir = (self.learner_dir + 1) % 4
                    next_next_dir = (self.learner_dir + 2) % 4

                next_pos = self.learner_pos + DIR_TO_VEC[next_dir]  # Turn
                next_next_pos = self.learner_pos + DIR_TO_VEC[next_next_dir]  # U-turn

                # Turn OR U-turn
                proba_dist[action] = 0.5 * (
                    np.exp(
                        -(
                            distance_map[rf_idx, goal_color, next_pos[0], next_pos[1]]
                            - const
                        )
                        / self.lambd
                    )
                    + np.exp(
                        -(
                            distance_map[
                                rf_idx, goal_color, next_next_pos[0], next_next_pos[1]
                            ]
                            - const
                        )
                        / self.lambd
                    )
                )

            else:
                # Forward
                next_dir = self.learner_dir
                next_pos = self.learner_pos + DIR_TO_VEC[next_dir]
                proba_dist[action] = np.exp(
                    -(
                        distance_map[rf_idx, goal_color, next_pos[0], next_pos[1]]
                        - const
                    )
                    / self.lambd
                )
        # Normalize
        proba_dist /= proba_dist.sum()

        assert not np.isnan(proba_dist[0])

        return proba_dist

    def obj_in_front(self, rf_idx: int, obj_idx: int) -> bool:
        dx, dy = 0, 0
        if self.learner_dir == 0:
            dx = 1
        elif self.learner_dir == 2:
            dx = -1
        elif self.learner_dir == 3:
            dy = -1
        elif self.learner_dir == 1:
            dy = 1

        return (
            self.learner_beliefs[
                rf_idx, self.learner_pos[0] + dx, self.learner_pos[1] + dy, obj_idx
            ]
            == 1
        )

    def compute_obstacle_grid(self, rf_idx: int) -> np.ndarray:
        one_hot = np.zeros(2 + self.num_colors * 2)
        one_hot[0] = 1
        return np.ones((self.gridsize, self.gridsize)) - np.all(
            self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2
        )

    def update_distance_goal(self, goal_color: int, rf_idx: int) -> None:
        goal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1)
        grid = self.compute_obstacle_grid(rf_idx)
        grid[goal_pos[0], goal_pos[1]] = 0
        # Update distance to the goal
        self.distance_goal[rf_idx, goal_color, :] = Dijkstra(
            grid, goal_pos[0], goal_pos[1]
        )

    def update_distance_subgoal(self, goal_color: int, rf_idx: int) -> None:
        subgoal_pos = np.where(
            self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1
        )
        grid = self.compute_obstacle_grid(rf_idx)
        grid[subgoal_pos[0], subgoal_pos[1]] = 0
        # Update distance to the subgoal
        self.distance_subgoal[rf_idx, goal_color, :] = Dijkstra(
            grid, subgoal_pos[0], subgoal_pos[1]
        )

    def learner_policy(self, goal_color: int, rf_idx: int) -> np.ndarray:
        if self.learner_step_count == 0:
            proba_dist = np.zeros(self.Na)
            proba_dist[4] = 1  # unused (to get first observation)
            return proba_dist

        # Subgoal (key) in front of the learner
        if self.obj_in_front(rf_idx, obj_idx=2 + (goal_color * 2) + 1):
            self.LOG.append("key in front")
            self.learner_reached_subgoal[goal_color, rf_idx] = True
            self.learner_going_to_subgoal[goal_color, rf_idx] = False

            proba_dist = np.zeros(self.Na)
            proba_dist[3] = 1  # Pickup the subgoal (key)
            return proba_dist

        # Goal (door) in front of the learner
        if (
            self.obj_in_front(rf_idx, obj_idx=2 + (2 * goal_color))
            and self.learner_reached_subgoal[goal_color, rf_idx]
        ):
            self.LOG.append("door in front")
            self.learner_going_to_goal[goal_color, rf_idx] = False

            proba_dist = np.zeros(self.Na)
            proba_dist[5] = 1  # Open the goal (door)
            return proba_dist

        # If know where is the subgoal (key) & not already have subgoal (key) & not already going to the subgoal (key) --> go to the subgoal (key)
        if (
            (self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1).any()
            and not self.learner_reached_subgoal[goal_color, rf_idx]
            and not self.learner_going_to_subgoal[goal_color, rf_idx]
        ):
            subgoal_pos = np.where(
                self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1
            )
            # Obstacle grid
            grid = self.compute_obstacle_grid(rf_idx)
            grid[subgoal_pos[0], subgoal_pos[1]] = 0

            path = A_star_algorithm(self.learner_pos, subgoal_pos, grid)

            if path is not None:  # First time computing the distance map
                # Compute distance to the subgoal
                self.distance_subgoal[rf_idx, goal_color, :] = Dijkstra(
                    grid, subgoal_pos[0], subgoal_pos[1]
                )
                # Set variable
                self.learner_going_to_subgoal[goal_color, rf_idx] = True
                # Return policy
                return self.learner_policy(goal_color, rf_idx)

        # If know where is the goal (door) & has subgoal (key) & not already going to the goal (door) --> go to the goal (door)
        elif (
            (self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1).any()
            and self.learner_reached_subgoal[goal_color, rf_idx]
            and not self.learner_going_to_goal[goal_color, rf_idx]
        ):
            goal_pos = np.where(
                self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1
            )
            # Obstacle grid
            one_hot = np.zeros(2 + self.num_colors * 2)
            one_hot[0] = 1
            grid = self.compute_obstacle_grid(rf_idx)
            grid[goal_pos[0], goal_pos[1]] = 0

            path = A_star_algorithm(self.learner_pos, goal_pos, grid)

            if path is not None:  # First time computing the distance map
                # Compute distance to the subgoal
                self.distance_goal[rf_idx, goal_color, :] = Dijkstra(
                    grid, goal_pos[0], goal_pos[1]
                )
                # Set variable
                self.learner_going_to_goal[goal_color, rf_idx] = True
                # Return action
                return self.learner_policy(goal_color, rf_idx)

        # Going to the subgoal --> greedy wrt to distance to the subgoal
        if self.learner_going_to_subgoal[goal_color, rf_idx]:
            return self.learner_greedy_policy(
                obj="subgoal", rf_idx=rf_idx, goal_color=goal_color
            )

        # Going to the goal --> greedy wrt to distance to the goal
        if self.learner_going_to_goal[goal_color, rf_idx]:
            return self.learner_greedy_policy(
                obj="goal", rf_idx=rf_idx, goal_color=goal_color
            )

        # Nothing to do --> Action that maximizes the exploration
        return self.learner_exploration_policy(goal_color, rf_idx)

    def update_knowledge(
        self,
        learner_pos: tuple,
        learner_dir: int,
        learner_step_count: int,
        rf_idx: int | None = None,
    ) -> None:
        self.learner_pos = learner_pos
        self.learner_dir = learner_dir
        self.learner_step_count += 1
        assert self.learner_step_count == learner_step_count

        if rf_idx is None:
            for rf_idx in range(self.num_rf):
                # Update what the learner knows about the env
                self.update_learner_belief(rf_idx)
        else:
            self.update_learner_belief(rf_idx)

    def observe(self, action: int) -> None:
        self.LOG.append(f"step t={self.learner_step_count}")
        self.LOG.append(f"True action {action}")

        for rf_idx in range(self.num_rf):
            for goal_color in range(self.num_colors):
                # Predict policy of the learner
                predicted_policy = self.learner_policy(goal_color, rf_idx)
                rf = self.rf_values[rf_idx]
                if goal_color == 0:
                    self.LOG.append(
                        f"agent_pos {self.env.agent_pos} dir {self.env.agent_dir} rf {rf} goal_color {goal_color} policy {np.round(predicted_policy, 4)}"
                    )

                # Bayesian update
                self.beliefs[goal_color, rf_idx] *= predicted_policy[action]

        self.beliefs /= self.beliefs.sum()
        self.LOG.append(f"pred {list(np.around(self.beliefs, 4))}")

    def predicted_reward(self, demo: list, goal_color: int, rf_idx: int) -> float:
        current_receptve_field = self.env.agent_view_size

        # Reset env AND estimate beliefs of the learner
        self.env.agent_view_size = self.rf_values[rf_idx]
        self.env.reset_grid()
        self.init_env(self.env)

        self.learner_step_count = 0

        if len(demo) > 0:
            # Add first unused action to get the first observation
            demo = [4] + demo
        else:
            demo = demo

        # Simulate the learner observing the demo
        for a in demo:
            action = Actions(a)
            _, _, _, _, _ = self.env.step(action)
            self.update_knowledge(
                self.env.agent_pos, self.env.agent_dir, self.env.step_count, rf_idx
            )

        # Simulate the learner playing on the env AFTER seen the demo
        self.env.reset_grid()
        self.learner_step_count = 0
        terminated = False
        while (not terminated) and (self.env.step_count < self.env.max_steps):
            a = draw(self.learner_policy(goal_color, rf_idx))
            action = Actions(a)
            _, reward, terminated, _, _ = self.env.step(action)
            self.update_knowledge(
                self.env.agent_pos, self.env.agent_dir, self.env.step_count, rf_idx
            )

        # Reset env
        self.env.agent_view_size = current_receptve_field
        self.env.reset_grid()

        # Return the predicted reward
        return reward

    def select_demo(
        self,
        cost_function: Callable[[int, int], float] = lambda x, l: exp_cost(
            l - x, l, alpha=0.3, beta=5
        ),
    ) -> list:
        goal_color_belief = np.sum(self.beliefs, axis=1)
        argmax_set = np.where(np.isclose(goal_color_belief, np.max(goal_color_belief)))[
            0
        ]
        pred_goal_color = np.random.choice(argmax_set)
        demos = []
        for rf in self.rf_values:
            demo = generate_demo(self.env, rf, pred_goal_color)
            demos.append(demo)

        # Compute longest demo
        demo_all = generate_demo_all(self.env)
        l_max = len(demo_all)

        demos.append(demo_all)

        predicted_utility = []
        for demo_idx, demo in enumerate(demos):
            pred_u = 0
            for rf_idx_demo, _ in enumerate(self.rf_values):
                hat_r = self.predicted_reward(demo, pred_goal_color, rf_idx_demo)
                cost = cost_function(len(demo), l_max)
                pred_u += (hat_r - cost) * self.beliefs[pred_goal_color, rf_idx_demo]
            predicted_utility.append(pred_u)

        argmax_set = np.where(np.isclose(predicted_utility, np.max(predicted_utility)))[
            0
        ]
        demo_idx = np.random.choice(argmax_set)

        predicted_best_utility = np.max(predicted_utility)

        return demos[demo_idx], demo_idx, predicted_best_utility, demos
