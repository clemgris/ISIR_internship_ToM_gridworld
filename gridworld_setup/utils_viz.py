import matplotlib.pyplot as plt
import numpy as np

from matplotlib.backends.backend_agg import FigureCanvasAgg
import matplotlib.gridspec as gridspec
from IPython.display import clear_output
from PIL import Image
import csv
import pickle

from minigrid.core.constants import IDX_TO_COLOR

from learner import BayesianLearner
from bayesian_teacher import AlignedBayesianTeacher, BayesianTeacher
from utils import Shannon_entropy

##
# Visualization
##

def plot_grid(start, num, size, alpha=0.5):
    idx = np.linspace(start, size, num)
    for x in idx:
        plt.plot([x, x], [start, size], alpha=alpha, c='gray')
        plt.plot([start, size], [x, x], alpha=alpha, c='gray')

def plot_agent_play(pos: tuple, dir: int, size: float=120) -> None:
    if dir == 0:
        marker = ">"
    elif dir == 1:
        marker = "v"
    elif dir == 2:
        marker = "<"
    elif dir == 3:
        marker = "^"
    plt.scatter(pos[0], pos[1], marker=marker, c='r', s=size)

def plot_agent_obs(pos: tuple, GRID_SIZE: int, img: np.ndarray, hide: bool=False, size: float | None=None) -> None:
    ratio = img.shape[0] / GRID_SIZE
    if size is None:
        size = ratio * 0.5
    im_agent_pos =np.array([(pos[0] + 0.5) * ratio, (pos[1] + 0.5) * ratio]).astype('int')
    if hide:
        plt.scatter(im_agent_pos[0], im_agent_pos[1], color=rgb_to_hex((76, 76, 76)), marker='s', s=size)
    plt.scatter(im_agent_pos[0], im_agent_pos[1], c='w', marker='*', s=size)

def plot_error_episode_length(colors: np.ndarray, rf_values: list, num_colors: int, dict: dict) -> None:
    labels = np.concatenate((np.array(rf_values)[:-1], np.array(['full obs'])))
    for rf_idx, receptive_field in reversed(list(enumerate(rf_values))):
        all_length = []
        all_accuracy = []
        for goal_color in range(num_colors):
            all_length += dict[receptive_field][goal_color]['length']
            all_accuracy += dict[receptive_field][goal_color]['accuracy']['rf']

        bins = np.arange(0, (np.max(all_length) // 20 + 1) * 20 + 1, 20)

        mean_accuracy = []
        std_accuracy = []
        n = []
        for i in range(len(bins) - 1):
            lower_bound = bins[i]
            upper_bound = bins[i + 1]
            filtered_accuracy = [acc for dist, acc in zip(all_length, all_accuracy) if lower_bound <= dist <= upper_bound]
            mean_accuracy.append(np.mean(filtered_accuracy))
            std_accuracy.append(np.std(filtered_accuracy))
            n.append(len(filtered_accuracy))

        
        plt.bar(range(len(bins) - 1), mean_accuracy, yerr=1.96 * np.array(std_accuracy) / np.sqrt(np.array(n)),
                color=colors[rf_idx], label=f'rf={labels[rf_idx]}')

        plt.xlabel('Length of the observed episode')
        plt.ylabel('Mean Accuracy (MAP)')
        plt.title('Mean accuracy (MAP) per episode length')

        plt.xticks(range(len(bins) - 1), [f'[{bins[i]},{bins[i + 1]}]' for i in range(len(bins) - 1)])

    plt.plot([-0.5, len(bins) - 1.5], [1, 1], label='Max', ls='--', c='k')
    plt.legend()

def rgb_to_hex(rgb):
    r, g, b = [max(0, min(255, int(channel))) for channel in rgb]
    # Convert RGB to hexadecimal color code (i.e. map to color type in python)
    hex_code = '#{:02x}{:02x}{:02x}'.format(r, g, b)
    return hex_code

##
# Display for Jupiter Notebook
##

def display_learner_play(GRID_SIZE: int, learner: BayesianLearner, size: int | None=None) -> list:
    ii = 0
    images = []
    while not learner.terminated:
        
        # Interaction
        _ = learner.play(size=1)

        fig = plt.figure(figsize=(10,5))
        fig.add_subplot(1,2,1)
        plt.imshow(learner.env.render())
        plt.title(f'Agent (t={ii})')
        plt.axis('off')

        fig.add_subplot(1,2,2)
        learner_beliefs_image = Shannon_entropy(learner.beliefs, axis=2) / (Shannon_entropy( 1 / 4 * np.ones(4)) + 0.2)
        plt.imshow(learner_beliefs_image.T, vmin=0., vmax=1., cmap='gray')
        plot_agent_play(learner.env.agent_pos, learner.env.agent_dir, size=size)
        plot_grid(-.5, GRID_SIZE + 1, GRID_SIZE - 0.5, alpha=0.3)
        # plt.colorbar(image)
        plt.title('Entropy learner beliefs')
        plt.axis('off')

        canvas = FigureCanvasAgg(fig)
        canvas.draw()

        # Get the image buffer as a PIL image
        pil_image = Image.frombytes('RGB', canvas.get_width_height(), canvas.tostring_rgb())
        images.append(pil_image)

        clear_output(wait=True)
        plt.show(fig)

        ii += 1
    return images

def display_learner_play_teacher_infer(GRID_SIZE: int, learner: BayesianLearner, 
                                       teacher: AlignedBayesianTeacher | BayesianTeacher, 
                                       num_colors: int=4) -> list:
    learner.env.highlight = True
    ii = 0
    images = []
    while not learner.terminated:
        
        # Interaction
        agent_pos = learner.env.agent_pos
        agent_dir = learner.env.agent_dir
        teacher.update_knowledge(learner_pos=agent_pos, learner_dir=agent_dir, learner_step_count=ii)
        traj = learner.play(size=1)
        teacher.observe(action=traj[0])

        fig = plt.figure(figsize=(20,5))
        fig.add_subplot(1,3,1)
        plt.imshow(learner.env.render())
        plt.title(f'Agent (t={ii})')
        plt.axis('off')

        fig.add_subplot(1,3,2)
        learner_beliefs_image = Shannon_entropy(learner.beliefs, axis=2) / (Shannon_entropy( 1 / 4 * np.ones(4)) + 0.2)
        image = plt.imshow(learner_beliefs_image.T, vmin=0., vmax=1., cmap='gray')
        plot_agent_play(teacher.env.agent_pos, teacher.env.agent_dir)
        plot_grid(-.5, GRID_SIZE + 1, GRID_SIZE - 0.5, alpha=0.3)
        # plt.colorbar(image)
        plt.title('Entropy learner beliefs')
        plt.axis('off')

        fig.add_subplot(1, 3, 3)
        plt.imshow(teacher.beliefs.T, vmin=0., vmax=1.)
        image = plt.imshow(teacher.beliefs.T, vmin=0., vmax=1.)
        plt.colorbar(image)
        plt.xticks(range(0, num_colors), [IDX_TO_COLOR[i] for i in range(1, num_colors + 1)])
        plt.yticks(range(0, len(teacher.rf_values)), teacher.rf_values)
        plt.title(f'Teacher belief about the learner \n {teacher.__class__.__name__}')
        plt.ylabel('Receptive field')
        plt.xlabel('Goal color')
        plot_grid(-.5, 5, 3.5)
        # plt.grid(True, which='major', linewidth=0.5)

        canvas = FigureCanvasAgg(fig)
        canvas.draw()

        # Get the image buffer as a PIL image
        pil_image = Image.frombytes('RGB', canvas.get_width_height(), canvas.tostring_rgb())
        images.append(pil_image)

        clear_output(wait=True)
        plt.show(fig)

        ii += 1
    return images

def display_learner_play_teacher_infer_blind(learner: BayesianLearner, 
                                             teacher: AlignedBayesianTeacher | BayesianTeacher, 
                                             num_colors: int=4) -> list:
    learner.env.highlight = False
    ii = 0
    images = []
    while not learner.terminated:
        
        # Interaction
        agent_pos = learner.env.agent_pos
        agent_dir = learner.env.agent_dir
        teacher.update_knowledge(learner_pos=agent_pos, learner_dir=agent_dir, learner_step_count=ii)
        traj = learner.play(size=1)
        teacher.observe(action=traj[0])

        fig = plt.figure(figsize=(10,5))
        fig.add_subplot(1,2,1)
        plt.imshow(learner.env.render())
        plt.title(f'Agent (t={ii})')
        plt.axis('off')

        fig.add_subplot(1, 2, 2)
        plt.imshow(teacher.beliefs.T, vmin=0., vmax=1.)
        image = plt.imshow(teacher.beliefs.T, vmin=0., vmax=1.)
        plt.colorbar(image)
        plt.xticks(range(0, num_colors), [IDX_TO_COLOR[i] for i in range(1, num_colors + 1)])
        plt.yticks(range(0, len(teacher.rf_values)), teacher.rf_values)
        plt.title(f'Teacher belief about the learner \n {teacher.__class__.__name__}')
        plt.ylabel('Receptive field')
        plt.xlabel('Goal color')
        plot_grid(-.5, 5, 3.5)
        # plt.grid(True, which='major', linewidth=0.5)

        canvas = FigureCanvasAgg(fig)
        canvas.draw()

        # Get the image buffer as a PIL image
        pil_image = Image.frombytes('RGB', canvas.get_width_height(), canvas.tostring_rgb())
        images.append(pil_image)

        clear_output(wait=True)
        plt.show(fig)

        ii += 1
    return images

def display_learner_obs_demo(GRID_SIZE: int, learner: BayesianLearner):
    learner.env.highlight = True
    ii = 0
    images = []
    for frame in learner.render_frames_observation:

        fig = plt.figure(figsize=(10,5))
        fig.add_subplot(1,2,1)
        plt.imshow(frame)
        plot_agent_obs(learner.pos[ii], GRID_SIZE, frame, hide=True)
        plt.title(f'Demonstration (t={ii}) (teleoperate)')
        plt.axis('off')

        fig.add_subplot(1,2,2)
        learner_beliefs_image = learner.render_beliefs_observation[ii]
        plt.imshow(learner_beliefs_image, vmin=0., vmax=1., cmap='gray')
        plot_grid(-.5, GRID_SIZE + 1, GRID_SIZE - 0.5, alpha=0.3)
        plot_agent_obs(learner.pos[ii], GRID_SIZE, learner_beliefs_image, hide=False, size=20)
        plt.title('Entropy learner beliefs')
        plt.axis('off')

        canvas = FigureCanvasAgg(fig)
        canvas.draw()

        # Get the image buffer as a PIL image
        pil_image = Image.frombytes('RGB', canvas.get_width_height(), canvas.tostring_rgb())
        images.append(pil_image)

        clear_output(wait=True)
        plt.show(fig)

        ii += 1
    return images

def save_LOG(filename: str, agent: BayesianTeacher | AlignedBayesianTeacher | BayesianLearner) -> None:

    with open(filename, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)

        for sentence in agent.LOG:
            writer.writerow([sentence])

def display_ToM_hist(GRID_SIZE: int, load_filename: str, save_filename: str,
                     N: int, lambd: float,
                     rf_values_basic: list=[3,5,7], num_colors: int=4) -> None:
    
    rf_values = rf_values_basic + [GRID_SIZE]
    
    with open(load_filename, 'rb') as f:
        DICT = pickle.load(f)
    dict = DICT[GRID_SIZE]

    fig = plt.figure(figsize=(15, 10))
    gs = gridspec.GridSpec(2, 3, width_ratios=[1, 1, 0.1])

    # First row, first column
    ax1 = plt.subplot(gs[0, 0])
    confusion_matrix = np.zeros((len(rf_values), len(rf_values)))
    for rf_idx,receptive_field in enumerate(rf_values):
        for goal_color in range(num_colors):
            
            for beliefs in dict[receptive_field][goal_color]['beliefs']:
                    confusion_matrix[rf_idx, :] += beliefs[goal_color, :]

    confusion_matrix /= num_colors * N
    plt.imshow(confusion_matrix, vmin=0., vmax=1.)
    images = plt.imshow(confusion_matrix, vmin=0., vmax=1., cmap='plasma')
    plt.colorbar(images)
    plt.title('Confusion matrix')
    plt.ylabel('Receptive field')
    plt.xlabel('Receptive field')
    plt.xticks(range(0, len(rf_values)), rf_values)
    plt.yticks(range(0, len(rf_values)), rf_values)

    # First row, second column
    ax2 = plt.subplot(gs[0, 1])

    colors = [np.array([149, 183, 226]) / 255, 'blue', np.array([52, 85, 156]) / 255, 'yellowgreen']

    mean_all = np.zeros(len(rf_values))
    std_all = np.zeros(len(rf_values))
    for rf_idx, rf in enumerate(rf_values):
        all_acc = []
        for goal_color in range(num_colors):
            all_acc += dict[rf][goal_color]['accuracy']['rf']
        mean_all[rf_idx] = np.mean(all_acc)
        std_all[rf_idx] = 1.96 * np.std(all_acc) / np.sqrt(len(all_acc))

    plt.bar(np.array(rf_values).astype(str), mean_all, width=.9, yerr=std_all, color=colors)
    plt.plot([-0.5, len(rf_values)-0.5], [1, 1], c='k', label='Max', ls='--')
    plt.ylabel('Accuracy (MAP)')
    plt.xlabel('Receptive field')
    plt.legend()
    plt.title('Accuracy (MAP) per receptive field')

    ax3 = plt.subplot(gs[1, :])
    plot_error_episode_length(colors=colors, rf_values=rf_values, num_colors=num_colors, dict=dict)

    # plt.tight_layout()

    fig.suptitle(f'Analysis GRID_SIZE={GRID_SIZE}, $\lambda$={lambd}', fontweight='bold')

    fig.savefig(save_filename);

def display_ToM_errorbar(load_filename: str, save_filename: str, lambd: float,
                         rf_values_basic: list=[3,5,7], num_colors: int=4) -> None:
    
    fig = plt.figure(figsize=(10,5))
    colors = [np.array([149, 183, 226]) / 255, 'blue', np.array([52, 85, 156]) / 255, 'yellowgreen']

    with open(load_filename, 'rb') as f:
        DICT = pickle.load(f)
    grid_size_values = DICT.keys()

    for ii,GRID_SIZE in enumerate(grid_size_values):
        dict = DICT[GRID_SIZE]
        rf_values = np.array(rf_values_basic + [GRID_SIZE])
        labels = np.concatenate((np.array(rf_values)[:-1], np.array(['full obs'])))
        
        for rf_idx, rf in enumerate(rf_values):
            all_acc = []
            for goal_color in range(num_colors):
                all_acc += dict[rf][goal_color]['accuracy']['rf']
            if ii == 5:
                plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / (np.sqrt(len(all_acc))), color=colors[rf_idx], fmt="o", label=f'rf={labels[rf_idx]}')
            else:
                plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / (np.sqrt(len(all_acc))), color=colors[rf_idx], fmt="o")
                
    plt.plot([0, 5], [1, 1], label='Max', ls='--', c='k')
    plt.xticks(np.arange(len(grid_size_values)), grid_size_values)
    plt.xlabel('Grid size')
    plt.ylabel('Accuracy (MAP)')
    plt.title(f'Mean accuracy (MAP) as a function of the environment size \n $\lambda$={lambd}')
    plt.legend(loc=2)
    plt.ylim(0.,1.05)

    fig.savefig(save_filename);


def display_all_ToM(lamd_values: list, grid_size_values: list, 
                    rf_values_basic: list=[3,5,7], num_colors: int=4) -> None:
    markers = ['*', '^', 'o', 's', 'd', 'h', 'x', 'v']
    colors = ['gold', 'orange', 'orangered', 'magenta', 'purple', 'blue', 'seagreen', 'slategrey']


    plt.figure(figsize=(15,6))

    for kk, lambd in enumerate(lamd_values):
        with open(f'./stats/lambda_{lambd}/stats_outputs_lambd_{lambd}.pickle', 'rb') as f:
            DICT = pickle.load(f)
        for ii,GRID_SIZE in enumerate(grid_size_values):
            dict = DICT[GRID_SIZE]
            rf_values = np.array(rf_values_basic + [GRID_SIZE])
            labels = np.concatenate((np.array(rf_values)[:-1], np.array(['full obs'])))
            
            all_acc = []
            for rf_idx, rf in enumerate(rf_values):
                for goal_color in range(num_colors):
                    all_acc += dict[rf][goal_color]['accuracy']['rf']
            if ii == 5:
                plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / np.sqrt(len(all_acc)), color=colors[kk], fmt=markers[kk], label=f'$\lambda$={lambd}')
            else:
                plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / np.sqrt(len(all_acc)), color=colors[kk], fmt=markers[kk])

    with open(f'./stats/aligned/stats_outputs_aligned.pickle', 'rb') as f:
        DICT = pickle.load(f)

    kk += 1
    for ii,GRID_SIZE in enumerate(grid_size_values):
        dict = DICT[GRID_SIZE]
        rf_values = np.array(rf_values_basic + [GRID_SIZE])
        labels = np.concatenate((np.array(rf_values)[:-1], np.array(['full obs'])))
        
        all_acc = []
        for rf_idx, rf in enumerate(rf_values):
            for goal_color in range(num_colors):
                all_acc += dict[rf][goal_color]['accuracy']['rf']
        if ii == 5:
            plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / np.sqrt(len(all_acc)), color=colors[kk], fmt=markers[kk], label=f'Aligned')
        else:
            plt.errorbar(ii, np.mean(all_acc), yerr=1.96 * np.std(all_acc) / np.sqrt(len(all_acc)), color=colors[kk], fmt=markers[kk])

    plt.plot([0, len(grid_size_values)-1], [1, 1], ls='--', label='Max', c='k')
    plt.xticks(np.arange(len(grid_size_values)), grid_size_values)
    plt.xlabel('Grid size')
    plt.ylabel('RF-inference accuracy (MAP)')
    plt.title('Mean RF-inference accuracy per Boltzmann temperature parameter $\lambda$')
    plt.legend()
    plt.legend(loc=2);