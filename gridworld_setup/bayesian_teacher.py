from minigrid.core.constants import DIR_TO_VEC

from environment import MultiGoalsEnv
from utils import *

import numpy as np
from queue import SimpleQueue

##
# Bayesian teacher that knows rational learner
##

class BayesianTeacher:

    def __init__(self,
                 env: MultiGoalsEnv,
                 num_colors: int=4,
                 rf_values: np.ndarray=np.array([3,5,7]),
                 Na: int=6,
                 lambd: float=0.5
                 ) -> None:
        
        self.env = env
        self.Na = Na

        # Boltzmann temperature parameter (confidence in greedy)
        self.lambd = lambd

        self.num_colors = num_colors
        self.num_rf = len(rf_values)
        self.rf_values = rf_values
        self.gridsize = self.env.height

        self.beliefs = 1. / ( num_colors * len(rf_values)) * np.ones((num_colors, len(rf_values)))
        self.learner_beliefs = 1. / (2 + 2 * num_colors) * np.ones((len(rf_values), self.gridsize, self.gridsize, 2 + 2 * num_colors))

        self.learner_going_to_subgoal = np.zeros((self.num_colors, self.num_rf), dtype=bool)
        self.learner_going_to_goal = np.zeros((self.num_colors, self.num_rf), dtype=bool)
        self.learner_reached_subgoal = np.zeros((self.num_colors, self.num_rf), dtype=bool)
        self.learner_step_count = -1

        self.distance_subgoal = np.zeros((self.num_rf, self.num_colors, self.gridsize, self.gridsize))
        self.distance_goal = np.zeros((self.num_rf, self.num_colors, self.gridsize, self.gridsize))

        self.LOG = []

    def compute_learner_obs(self, receptive_field: int) -> np.ndarray:
        
        # Facing right
        if self.learner_dir == 0:
            topX = self.learner_pos[0]
            topY = self.learner_pos[1] - receptive_field // 2
        # Facing down
        elif self.learner_dir == 1:
            topX = self.learner_pos[0] - receptive_field // 2
            topY = self.learner_pos[1]
        # Facing left
        elif self.learner_dir == 2:
            topX = self.learner_pos[0] - receptive_field + 1
            topY = self.learner_pos[1] - receptive_field // 2
        # Facing up
        elif self.learner_dir == 3:
            topX = self.learner_pos[0] - receptive_field // 2
            topY = self.learner_pos[1] - receptive_field + 1
        
        grid = self.env.grid.slice(topX, topY, receptive_field, receptive_field)
        for _ in range(self.learner_dir + 1):
            grid = grid.rotate_left()
        vis_mask = np.ones(shape=(grid.width, grid.height), dtype=bool)

        obs = grid.encode(vis_mask)

        return obs

    def update_learner_belief(self, rf_idx: int) -> None:
        
        receptive_field = self.rf_values[rf_idx]
        obs = self.compute_learner_obs(receptive_field)

        f_vec = DIR_TO_VEC[self.learner_dir]
        dir_vec = DIR_TO_VEC[self.learner_dir]
        dx, dy = dir_vec
        r_vec =  np.array((-dy, dx))
        top_left = (
            self.learner_pos
            + f_vec * (receptive_field - 1)
            - r_vec * (receptive_field // 2)
        )
        new_cells = 0
        # For each cell in the visibility mask
        for vis_j in range(0, receptive_field):
            for vis_i in range(0, receptive_field):

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
                    new_cells +=1

                self.learner_beliefs[rf_idx, abs_i, abs_j, :] = one_hot

        if new_cells > 0:
            for goal_color in range(self.num_colors):
                # Additional info --> update distance map
                if self.learner_going_to_subgoal[goal_color, rf_idx] and not self.learner_reached_subgoal[goal_color, rf_idx]:
                    self.LOG.append('Recompute distances to subgoal')
                    self.update_distance_subgoal(goal_color, rf_idx)

                elif self.learner_going_to_goal[goal_color, rf_idx]:
                    self.LOG.append('Recompute distances to goal')
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
        if (np.all(self.learner_beliefs[rf_idx, next_pos[0], next_pos[1], :] == one_hot_empty) or \
            np.all(self.learner_beliefs[rf_idx, next_pos[0], next_pos[1], :] == one_hot_subgoal)): # No obstacle in front
            prob_dist[2] = 1

        prob_dist /= prob_dist.sum()
        return prob_dist
    
    def learner_greedy_policy(self, obj: str, rf_idx: int, goal_color: int) -> np.ndarray:
        if obj == 'goal':
            distance_map = self.distance_goal
        elif obj == 'subgoal':
            distance_map = self.distance_subgoal
        else:
            raise ValueError('Unknown object for distance map')

        proba_dist = np.zeros(self.Na)
        for action in range(3):
            # Boltzman wrt distance to the goal
            if action in [0, 1]: # Turn left or right
                if action == 0:
                    next_dir = (self.learner_dir - 1) % 4
                    next_next_dir = (self.learner_dir - 2) % 4
                elif action == 1:
                    next_dir = (self.learner_dir + 1) % 4
                    next_next_dir = (self.learner_dir + 2) % 4            
            
                next_pos = self.learner_pos + DIR_TO_VEC[next_dir] # Turn 
                next_next_pos = self.learner_pos + DIR_TO_VEC[next_next_dir] # U-turn
                
                # Turn OR U-turn
                proba_dist[action] = 0.5 * (np.exp( - distance_map[rf_idx, goal_color, next_pos[0], next_pos[1]] / self.lambd) \
                                            + np.exp( - distance_map[rf_idx, goal_color, next_next_pos[0], next_next_pos[1]] / self.lambd))

            else:
                # Forward
                next_dir = self.learner_dir
                next_pos = self.learner_pos + DIR_TO_VEC[next_dir]
                proba_dist[action] = np.exp( - distance_map[rf_idx, goal_color, next_pos[0], next_pos[1]] / self.lambd)
        # Normalize
        proba_dist /= proba_dist.sum()
        
        assert(not np.isnan(proba_dist[0]))
            
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

        return self.learner_beliefs[rf_idx, self.learner_pos[0] + dx, self.learner_pos[1] + dy, obj_idx] == 1
    
    def compute_obstacle_grid(self, rf_idx: int) -> np.ndarray:
        one_hot = np.zeros(2 + self.num_colors * 2)
        one_hot[0] = 1
        return np.ones((self.gridsize, self.gridsize)) - np.all(self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2)
    
    def update_distance_goal(self, goal_color: int, rf_idx: int) -> None:
        goal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1)
        grid = self.compute_obstacle_grid(rf_idx)
        grid[goal_pos[0], goal_pos[1]] = 0
        # Update distance to the goal
        self.distance_goal[rf_idx, goal_color, :] = Dijkstra(grid, goal_pos[0], goal_pos[1])

    def update_distance_subgoal(self, goal_color: int, rf_idx: int) -> None:
        subgoal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1)
        grid = self.compute_obstacle_grid(rf_idx)
        grid[subgoal_pos[0], subgoal_pos[1]] = 0
        # Update distance to the subgoal
        self.distance_subgoal[rf_idx, goal_color, :] = Dijkstra(grid, subgoal_pos[0], subgoal_pos[1])

    
    def learner_policy(self, goal_color: int, rf_idx: int) -> np.ndarray:
            
        if self.learner_step_count == 0:
            proba_dist = np.zeros(self.Na)
            proba_dist[4] = 1 # unused (to get first observation)
            return proba_dist

        # Subgoal (key) in front of the learner
        if self.obj_in_front(rf_idx, obj_idx=2 + (goal_color * 2) + 1):
            self.LOG.append('key in front')
            self.learner_reached_subgoal[goal_color, rf_idx] = True
            self.learner_going_to_subgoal[goal_color, rf_idx] = False

            proba_dist = np.zeros(self.Na)
            proba_dist[3] = 1 # Pickup the subgoal (key)
            return proba_dist
        
        # Goal (door) in front of the learner
        if self.obj_in_front(rf_idx, obj_idx= 2 + (2 * goal_color)) and self.learner_reached_subgoal[goal_color, rf_idx]:
            self.LOG.append('door in front')
            self.learner_going_to_goal[goal_color, rf_idx] = False

            proba_dist = np.zeros(self.Na)
            proba_dist[5] = 1 # Open the goal (door)
            return proba_dist

        # If know where is the subgoal (key) & not already have subgoal (key) & not already going to the subgoal (key) --> go to the subgoal (key)
        if (self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1).any() and \
            not self.learner_reached_subgoal[goal_color, rf_idx] and \
            not self.learner_going_to_subgoal[goal_color, rf_idx]:
            
            subgoal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2 + 1] == 1)
            # Obstacle grid
            grid = self.compute_obstacle_grid(rf_idx)
            grid[subgoal_pos[0], subgoal_pos[1]] = 0
            
            path = A_star_algorithm(self.learner_pos, subgoal_pos, grid)

            if path is not None: # First time computing the distance map
                # Compute distance to the subgoal
                self.distance_subgoal[rf_idx, goal_color, :] = Dijkstra(grid, subgoal_pos[0], subgoal_pos[1])
                # Set variable
                self.learner_going_to_subgoal[goal_color, rf_idx] = True
                # Return policy
                return self.learner_policy(goal_color, rf_idx)

        # If know where is the goal (door) & has subgoal (key) & not already going to the goal (door) --> go to the goal (door)
        elif (self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1).any() and \
              self.learner_reached_subgoal[goal_color, rf_idx] and \
              not self.learner_going_to_goal[goal_color, rf_idx]:

            goal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + goal_color * 2] == 1)
            # Obstacle grid
            one_hot = np.zeros(2 + self.num_colors * 2)
            one_hot[0] = 1
            grid = self.compute_obstacle_grid(rf_idx)
            grid[goal_pos[0], goal_pos[1]] = 0

            path = A_star_algorithm(self.learner_pos, goal_pos, grid)

            if path is not None: # First time computing the distance map
                # Compute distance to the subgoal
                self.distance_goal[rf_idx, goal_color, :] = Dijkstra(grid, goal_pos[0], goal_pos[1])
                # Set variable
                self.learner_going_to_goal[goal_color, rf_idx] = True
                # Return action
                return self.learner_policy(goal_color, rf_idx)
        
        # Going to the subgoal --> greedy wrt to distance to the subgoal
        if self.learner_going_to_subgoal[goal_color, rf_idx]:
            return self.learner_greedy_policy(obj='subgoal', rf_idx=rf_idx, goal_color=goal_color)
        
        # Going to the goal --> greedy wrt to distance to the goal
        if self.learner_going_to_goal[goal_color, rf_idx]:
            return self.learner_greedy_policy(obj='goal', rf_idx=rf_idx, goal_color=goal_color)
        
        # Nothing to do 
        else:
            # Action that maximizes the exploration
            return self.learner_exploration_policy(goal_color, rf_idx)
        
    def update_knowledge(self, learner_pos: tuple, learner_dir: int, learner_step_count: int) -> None:
        self.learner_pos = learner_pos
        self.learner_dir = learner_dir
        self.learner_step_count += 1
        assert(self.learner_step_count == learner_step_count)
        
        for rf_idx in range(self.num_rf):
            # Update what the learner knows about the env
            self.update_learner_belief(rf_idx)

    def observe(self, action: int) -> None:

        self.LOG.append(f'step t = {self.learner_step_count}')
        self.LOG.append(f'true action a = {action}')
        
        for rf_idx in range(self.num_rf):
            for goal_color in range(self.num_colors):
                # Predict policy of the learner
                predicted_policy = self.learner_policy(goal_color, rf_idx)
                rf = self.rf_values[rf_idx]
                if goal_color == 0:
                    self.LOG.append(f'agent_pos {self.env.agent_pos} dir {self.env.agent_dir} rf {rf} goal_color {goal_color} policy {np.round(predicted_policy, 4)}')
                    
                # Bayesian update
                self.beliefs[goal_color, rf_idx] *= predicted_policy[action]

        self.beliefs /= self.beliefs.sum()
        self.LOG.append(f'pred {list(np.around(self.beliefs, 4))}')


##
# Bayesian teacher that knows rational learner & learner is using A* algo to compute the shortest path
##

class BayesianTeacherA_star:

    def __init__(self,
                 env: MultiGoalsEnv,
                 num_colors: int=4,
                 rf_values: np.ndarray=np.array([3,5,7,15]),
                 Na: int=6
                 ) -> None:
        
        self.env = env
        self.Na = 6

        self.num_colors = num_colors
        self.num_rf = len(rf_values)
        self.gridsize = self.env.height

        self.rf_values = rf_values
        
        self.beliefs = 1. / ( num_colors * len(rf_values)) * np.ones((num_colors, len(rf_values)))
        self.learner_beliefs = 1. / (2 + 2 * num_colors) * np.ones((len(rf_values), self.gridsize, self.gridsize, 2 + 2 * num_colors))

        self.learner_queue_actions = {}
        self.learner_queue_transitions = {}
        for goal_color in range(num_colors):
            for rf in rf_values:
                self.learner_queue_actions[goal_color] = {}
                self.learner_queue_transitions[goal_color] = {}
                self.learner_queue_actions[goal_color][rf] = SimpleQueue()
                self.learner_queue_transitions[goal_color][rf] = SimpleQueue()

        self.learner_going_to_subgoal = np.zeros((self.num_colors, self.num_rf), dtype=bool)
        self.learner_going_to_goal = np.zeros((self.num_colors, self.num_rf), dtype=bool)
        self.learner_reached_subgoal = np.zeros((self.num_colors, self.num_rf), dtype=bool)

    def compute_learner_obs(self, learner_pos: tuple, learner_dir: int, receptive_field: int) -> np.ndarray:
        
        # Facing right
        if learner_dir == 0:
            topX = learner_pos[0]
            topY = learner_pos[1] - receptive_field // 2
        # Facing down
        elif learner_dir == 1:
            topX = learner_pos[0] - receptive_field // 2
            topY = learner_pos[1]
        # Facing left
        elif learner_dir == 2:
            topX = learner_pos[0] - receptive_field + 1
            topY = learner_pos[1] - receptive_field // 2
        # Facing up
        elif learner_dir == 3:
            topX = learner_pos[0] - receptive_field // 2
            topY = learner_pos[1] - receptive_field + 1

        grid = self.env.grid.slice(topX, topY, receptive_field, receptive_field)
        vis_mask = np.ones(shape=(grid.width, grid.height), dtype=bool)

        obs = grid.encode(vis_mask)

        return obs

    def update_learner_belief(self, learner_pos: tuple, learner_dir: int, rf_idx: int) -> None:
        
        receptive_field = self.rf_values[rf_idx]

        obs = self.compute_learner_obs(learner_pos, learner_dir, receptive_field)

        f_vec = DIR_TO_VEC[learner_dir]
        dir_vec = DIR_TO_VEC[learner_dir]
        dx, dy = dir_vec
        r_vec =  np.array((-dy, dx))
        top_left = (
            learner_pos
            + f_vec * (receptive_field - 1)
            - r_vec * (receptive_field // 2)
        )

        # For each cell in the visibility mask
        for vis_j in range(0, receptive_field):
            for vis_i in range(0, receptive_field):

                # Compute the world coordinates of this cell
                abs_i, abs_j = top_left - (f_vec * vis_j) + (r_vec * vis_i)
                if abs_i < 0 or abs_i >= self.gridsize:
                    continue
                if abs_j < 0 or abs_j >= self.gridsize:
                    continue
                if learner_pos == (abs_i, abs_j):
                    continue

                one_hot = np.zeros(2 + 2 * self.num_colors)
                # Goal
                if obs[vis_i, vis_j, 0] == 4:
                    one_hot[2 + (obs[vis_i, vis_j, 1] - 1) * 2] = 1
                # Subgoal (key)
                elif obs[vis_i, vis_j, 0] == 5:
                    one_hot[2 + (obs[vis_i, vis_j, 1] - 1) * 2 + 1] = 1
                # Wall
                elif obs[vis_i, vis_j, 0] == 2:
                    one_hot[1] = 1
                # Nothing
                else:
                    one_hot[0] = 1
                
                self.learner_beliefs[rf_idx, abs_i, abs_j, :] = one_hot

    def compute_exploration_score(self, learner_dir: int, learner_pos: tuple, rf_idx: int) -> float:
        
        receptive_field = self.rf_values[rf_idx]
        
        f_vec = DIR_TO_VEC[learner_dir]
        dir_vec = DIR_TO_VEC[learner_dir]
        dx, dy = dir_vec
        r_vec =  np.array((-dy, dx))

        top_left = (
            learner_pos
            + f_vec * (receptive_field - 1)
            - r_vec * (receptive_field // 2)
        )

        exploration_score = 0

        # For each cell in the visibility mask
        for vis_j in range(0, receptive_field):
            for vis_i in range(0, receptive_field):

                # Compute the world coordinates of this cell
                abs_i, abs_j = top_left - (f_vec * vis_j) + (r_vec * vis_i)
                if abs_i < 0 or abs_i >= self.gridsize:
                    continue
                if abs_j < 0 or abs_j >= self.gridsize:
                    continue

                exploration_score += Shannon_entropy(self.learner_beliefs[rf_idx, abs_i, abs_j, :])
            
        return exploration_score
    
    def learner_best_exploration_action(self, learner_pos: tuple, learner_dir: int,
                                rf_idx: int, goal_color: int,
                                forced: bool=False) -> int:

        receptive_field = self.rf_values[rf_idx]

        # Action that maximizes the exploration
        scores = np.zeros(3)
        # Turn left
        scores[0] = self.compute_exploration_score(learner_dir=(learner_dir - 1) % 4, learner_pos=learner_pos, rf_idx=rf_idx)
        # Turn right
        scores[1] = self.compute_exploration_score(learner_dir=(learner_dir + 1) % 4, learner_pos=learner_pos, rf_idx=rf_idx)
        # Move forward
        next_pos = learner_pos + DIR_TO_VEC[learner_dir]
        if np.all(self.beliefs[next_pos[0], next_pos[1], :] == np.array([0, 1, 0, 0])) or \
            np.all(self.beliefs[next_pos[0], next_pos[1], :] == np.array([0, 0, 1, 0])): # Obstacle in front
            scores[2] = 0.
        else:
            scores[2] = self.compute_exploration_score(learner_dir=learner_dir, learner_pos=next_pos)

        argmax_set = np.where(np.isclose(scores, np.max(scores)))[0]

        # If actions better than the others
        if len(argmax_set) < 3 or forced:
            proba_dist = np.ones(self.Na)
            proba_dist[argmax_set] = 1
            proba_dist /= proba_dist.sum()

            return proba_dist
        
        # If all the actions are equal
        else:

            # Unexplored locations
            unexplored_pos = np.where(Shannon_entropy(self.beliefs, axis=2) != 0)
            # At least one unexplored position
            if len(unexplored_pos[0]) > 0:
                # Manhattan distance to the unexplored locations
                dist = np.array([Manhattan_dist(learner_pos, pos) for pos in zip(unexplored_pos[0], unexplored_pos[1])])
                # Closest unexplored locations
                argmin_set = np.where(np.isclose(dist, np.min(dist)))[0]
                dest_idx = np.random.choice(argmin_set)
                dest_pos = (unexplored_pos[0][dest_idx], unexplored_pos[1][dest_idx])

                # Obstacles grid
                one_hot = np.zeros(2 + self.num_colors * 2)
                one_hot[0] = 1
                grid = np.ones((self.gridsize, self.gridsize)) - np.all(self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2)
                grid[dest_pos[0], dest_pos[1]] = 0
                
                # If the intermediate exploratory goal is not reacheable change exploratory goal
                while dist[dest_idx] < 10e5:

                    path = A_star_algorithm(learner_pos, dest_pos, grid)

                    if path is None:
                        # Choose another exploratory goal
                        dist[dest_idx] = 10e5
                        argmin_set = np.where(np.isclose(dist, np.min(dist)))[0]
                        dest_idx = np.random.choice(argmin_set)
                        dest_pos = (unexplored_pos[0][dest_idx], unexplored_pos[1][dest_idx])

                        # Obstacles grid
                        one_hot = np.zeros(2 + self.num_colors * 2)
                        one_hot[0] = 1
                        grid = np.ones((self.gridsize, self.gridsize)) - np.all(self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2)
                        grid[dest_pos[0], dest_pos[1]] = 0
                    else:
                        # Add transitions to go to exploratory goal
                        for transition in path:
                            self.learner_queue_transitions[goal_color, receptive_field].put(transition)

                        return self.learner_policy()

            return self.learner_best_exploration_action(forced=True)
            

    def add_actions(self, learner_pos: tuple, pos_dest: tuple, learner_dir: int, goal_color: int, rf_idx: int) -> None:
        # Mapping position transition --> actions
        dx = learner_pos[0] - pos_dest[0]
        dy = learner_pos[1] - pos_dest[1]
        if dx < 0:
            if learner_dir == 0:
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 1:
                self.learner_queue_actions[goal_color, rf_idx].put(0)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 2:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 3:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)

        if dx > 0:
            if learner_dir == 0:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 1:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 2:
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 3:
                self.learner_queue_actions[goal_color, rf_idx].put(0)
                self.learner_queue_actions[goal_color, rf_idx].put(2)

        if dy < 0:
            if learner_dir == 0:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 1:
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 2:
                self.learner_queue_actions[goal_color, rf_idx].put(0)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 3:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)

        if dy > 0:
            if learner_dir == 0:
                self.learner_queue_actions[goal_color, rf_idx].put(0)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 1:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 2:
                self.learner_queue_actions[goal_color, rf_idx].put(1)
                self.learner_queue_actions[goal_color, rf_idx].put(2)
            elif learner_dir == 3:
                self.learner_queue_actions[goal_color, rf_idx].put(2)

    def obj_in_front(self, learner_pos: tuple, learner_dir: int, obj_idx: int) -> bool:

        dx, dy = 0, 0
        if learner_dir == 0:
            dx = 1
        elif learner_dir == 2:
            dx = -1
        elif learner_dir == 3:
            dy = -1
        elif learner_dir == 1:
            dy = 1

        return self.beliefs[learner_pos[0] + dx, learner_pos[1] + dy, obj_idx] == 1
        
    def learner_policy(self, learner_pos: tuple, learner_dir: int,
                       rf_idx: int, goal_color: int,
                       learner_step_count: int):

        receptive_field = self.rf_values[rf_idx]
            
        if learner_step_count == 0:
            proba_dist = np.zeros(self.Na)
            proba_dist[4] = 1 # unused (to get first observation)
            return proba_dist

        # Subgoal (key) in front of the learner
        if self.obj_in_front(obj_idx=3):

            self.learner_reached_subgoal[goal_color, rf_idx] = True
            self.learner_going_to_subgoal[goal_color, rf_idx] = False
            # Subgoal (key) reached --> empty queues
            while not self.learner_queue_transitions[goal_color, receptive_field].empty():
                _ = self.learner_queue_transitions[goal_color, receptive_field].get()
            while not self.learner_queue_actions[goal_color][receptive_field].empty():
                _ = self.learner_queue_actions[goal_color][receptive_field].get()
            proba_dist = np.zeros(self.Na)
            proba_dist[3] = 1 # Pickup the subgoal (key)
            return proba_dist
        
        # Goal (door) in front of the learner
        if self.obj_in_front(obj_idx=2) and self.learner_reached_subgoal[goal_color, rf_idx]:

            self.learner_going_to_goal[goal_color, rf_idx] = False
            # Goal (door) reached --> empty queues
            while not self.learner_queue_transitions[goal_color, receptive_field].empty():
                _ = self.learner_queue_transitions[goal_color, receptive_field].get()
            while not self.learner_queue_actions[goal_color][receptive_field].empty():
                _ = self.learner_queue_actions[goal_color][receptive_field].get()
            proba_dist = np.zeros(self.Na)
            proba_dist[5] = 1 # Open the goal (door)
            return proba_dist

        # If know where is the subgoal (key) & not already have subgoal (key) & not already going to the subgoal (key) --> go to the subgoal (key)
        if (self.learner_beliefs[rf_idx, :, :, 2 + (goal_color - 1) * 2 + 1] == 1).any() and \
            not self.learner_reached_subgoal[goal_color, rf_idx] and \
            not self.learner_going_to_subgoal[goal_color, rf_idx]:

            subgoal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + (goal_color - 1) * 2 + 1] == 1)
            # Obstacle grid # WARNING
            one_hot = np.zeros(2 + self.num_colors * 2)
            one_hot[0] = 1
            grid = np.ones((self.gridsize, self.gridsize)) - np.all(self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2)
            grid[subgoal_pos[0], subgoal_pos[1]] = 0
            
            path = A_star_algorithm(learner_pos, subgoal_pos, grid)

            if path is not None:
                # Empty queues
                while not self.learner_queue_transitions[goal_color, receptive_field].empty():
                    _ = self.learner_queue_transitions[goal_color, receptive_field].get()
                while not self.learner_queue_actions[goal_color][receptive_field].empty():
                    _ = self.learner_queue_actions[goal_color][receptive_field].get()
                # Add transitions to go to key
                for transition in path:
                    self.learner_queue_transitions[goal_color, receptive_field].put(transition)
                # Set variable
                self.learner_going_to_subgoal[goal_color, rf_idx] = True
                # Return action
                return self.learner_policy()

        # If know where is the goal (door) & has subgoal (key) & not already going to the goal (door) --> go to the goal (door)
        elif (self.learner_beliefs[rf_idx, :, :, 2 + (goal_color - 1) * 2] == 1).any() and \
              self.learner_reached_subgoal[goal_color, rf_idx] and \
              not self.learner_going_to_goal[goal_color, rf_idx]:

            goal_pos = np.where(self.learner_beliefs[rf_idx, :, :, 2 + (goal_color - 1) * 2] == 1)
            # Obstacle grid
            one_hot = np.zeros(2 + self.num_colors * 2)
            one_hot[0] = 1
            grid = np.ones((self.gridsize, self.gridsize)) - np.all(self.learner_beliefs[rf_idx, ...] == one_hot.reshape(1, 1, -1), axis=2)
            grid[goal_pos[0], goal_pos[1]] = 0

            path = A_star_algorithm(learner_pos, goal_pos, grid)

            if path is not None:
                # Empty queues
                while not self.learner_queue_transitions[goal_color, receptive_field].empty():
                    _ = self.learner_queue_transitions[goal_color, receptive_field].get()
                while not self.learner_queue_actions[goal_color][receptive_field].empty():
                    _ = self.learner_queue_actions[goal_color][receptive_field].get()
                # Add transitions to go to goal (door)
                for transition in path:
                    self.learner_queue_transitions[goal_color, receptive_field].put(transition)
                # Set variable
                self.learner_going_to_goal[goal_color, rf_idx] = True
                # Return action
                return self.learner_policy()
        
        # Action to be played
        if not self.learner_queue_actions[goal_color][receptive_field].empty():
            action = self.learner_queue_actions[goal_color][receptive_field].get()
            return action
        
        # Position to be reached
        if not self.learner_queue_transitions[goal_color, receptive_field].empty():
            pos_init, pos_dest = self.learner_queue_transitions[goal_color, receptive_field].get()
            
            # Sanity check
            assert(pos_init == learner_pos)

            # If not an obstacle --> add action to reach pos_dest
            one_hot = np.zeros(2 + self.num_colors * 2)
            one_hot[0] = 1
            if np.any(self.learner_beliefs[rf_idx, pos_dest[0], pos_dest[1], :] == one_hot):
                self.add_actions(learner_pos=learner_pos ,pos_dest=pos_dest,
                                 learner_dir=learner_dir,
                                 goal_color=goal_color, rf_idx=rf_idx)

            return self.learner_policy()
        
        # Nothing to do 
        else:
            # Action that maximizes the exploration
            return self.learner_best_exploration_action()
        
    def update_beliefs(self, learner_pos: tuple, learner_dir: int, learner_step_count: int) -> None:
        
        for goal_color in range(self.num_colors):
            for rf_idx in range(self.num_rf):
                # Bayesian update
                self.beliefs[goal_color, rf_idx] *= self.learner_policy(learner_pos, learner_dir, rf_idx, goal_color, learner_step_count)

        beliefs /= beliefs.sum()

    
