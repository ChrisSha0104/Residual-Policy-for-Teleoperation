# Copyright (c) 2022-2024, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This script demonstrates how to use the differential inverse kinematics controller with the simulator.

The differential IK controller can be configured in different modes. It uses the Jacobians computed by
PhysX. This helps perform parallelized computation of the inverse kinematics.

.. code-block:: bash

    # Usage
    ./isaaclab.sh -p source/standalone/tutorials/05_controllers/ik_control.py

"""

"""Launch Isaac Sim Simulator first."""

import argparse

from omni.isaac.lab.app import AppLauncher

# add argparse arguments
parser = argparse.ArgumentParser(description="Tutorial on using the differential IK controller.")
parser.add_argument("--robot", type=str, default="franka_panda", help="Name of the robot.")
parser.add_argument("--num_envs", type=int, default=1, help="Number of environments to spawn.")
# append AppLauncher cli args
AppLauncher.add_app_launcher_args(parser)
# parse the arguments
args_cli = parser.parse_args()

# launch omniverse app
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

"""Rest everything follows."""

import torch
import numpy as np
import os

import omni.isaac.lab.sim as sim_utils
from omni.isaac.lab.assets import AssetBaseCfg
from omni.isaac.lab.controllers import DifferentialIKController, DifferentialIKControllerCfg
from omni.isaac.lab.managers import SceneEntityCfg
from omni.isaac.lab.markers import VisualizationMarkers
from omni.isaac.lab.markers.config import FRAME_MARKER_CFG
from omni.isaac.lab.scene import InteractiveScene, InteractiveSceneCfg
from omni.isaac.lab.utils import configclass
from omni.isaac.lab.utils.assets import ISAAC_NUCLEUS_DIR
from omni.isaac.lab.utils.math import subtract_frame_transforms

from omni.isaac.lab.assets import Articulation, ArticulationCfg
from omni.isaac.lab.actuators.actuator_cfg import ImplicitActuatorCfg
from omni.isaac.lab.terrains import TerrainImporterCfg
import omni.isaac.lab.utils.math as math_utils
from omni.isaac.lab.assets import RigidObjectCfg, RigidObject
from omni.isaac.lab.sim.schemas.schemas_cfg import RigidBodyPropertiesCfg



##
# Pre-defined configs
##
from omni.isaac.lab_assets import FRANKA_PANDA_HIGH_PD_CFG, UR10_CFG  # isort:skip


@configclass
class FrankaCabinetScene(InteractiveSceneCfg):
    """Configuration for a cart-pole scene."""

    # ground plane
    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="plane",
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=1.0,
            restitution=0.0,
        ),
    )

    # lights
    dome_light = AssetBaseCfg(
        prim_path="/World/Light", spawn=sim_utils.DomeLightCfg(intensity=3000.0, color=(0.75, 0.75, 0.75))
    )

    # robot
    robot = ArticulationCfg(
        prim_path="{ENV_REGEX_NS}/Robot",
        spawn=sim_utils.UsdFileCfg(
            usd_path="/home/shuosha/Desktop/xarm7_with_gripper/xarm7_with_gripper.usd",
            activate_contact_sensors=False,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                disable_gravity=False, # add gravity
                max_depenetration_velocity=5.0,
            ),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                enabled_self_collisions=True, solver_position_iteration_count=12, solver_velocity_iteration_count=1
            ),
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            joint_pos={
                "joint1": -0.07,
                "joint2": -0.7,
                "joint3": -0.02,
                "joint4": 0.2,
                "joint5": -0.2,
                "joint6": 0.5,
                "joint7": 0.1,
                "drive_joint": 0.0,
                "left_finger_joint": 0.0,
                "left_inner_knuckle_joint": 0.0,
                "right_outer_knuckle_joint": 0.0,
                "right_finger_joint": 0.0,
               "right_inner_knuckle_joint": 0.0,
            },
            # pos=(0.0, 0.0, 0.0),
            # rot=(0.0, 0.0, 0.0, 1.0),
        ),
        actuators={
            "shoulder": ImplicitActuatorCfg(
                joint_names_expr=["joint[1-5]"],
                # effort_limit=87.0,
                # velocity_limit=2.175,
                stiffness=200,
                damping=80,
            ),
            "forearm": ImplicitActuatorCfg(
                joint_names_expr=["joint[6-7]"],
                # effort_limit=87.0,
                # velocity_limit=2.175,
                stiffness=200,
                damping=80,
            ),
            "xarm_hand": ImplicitActuatorCfg(
                joint_names_expr=[".*_joint"],
                # effort_limit=200.0,
                # velocity_limit=0.2,
                stiffness=200,
                damping=80,
            ),
        },
    )

    # mount
    cube = RigidObjectCfg(
            prim_path="{ENV_REGEX_NS}/Object",
            init_state=RigidObjectCfg.InitialStateCfg(
                pos=(0.3, 0.0, 0), 
                rot=(0, 0, 0, 1),
            ),
            spawn=sim_utils.UsdFileCfg(
                usd_path=f"{ISAAC_NUCLEUS_DIR}/Props/Blocks/DexCube/dex_cube_instanceable.usd", 
                scale=(0.8, 0.8, 0.8),
                rigid_props=RigidBodyPropertiesCfg(
                    solver_position_iteration_count=16,
                    solver_velocity_iteration_count=1,
                    max_angular_velocity=1000.0,
                    max_linear_velocity=1000.0,
                    max_depenetration_velocity=5.0,
                    disable_gravity=False,
                ),
            ),
        )

def run_simulator(sim: sim_utils.SimulationContext, scene: InteractiveScene):
    """Runs the simulation loop."""
    # Extract scene entities
    # note: we only do this here for readability.
    robot = scene["robot"]

    # Specify robot-specific parameters

    # ik controller
    diff_ik_cfg = DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method="dls")
    diff_ik_controller = DifferentialIKController(diff_ik_cfg, num_envs=scene.num_envs, device=sim.device)

    # markers
    frame_marker_cfg = FRAME_MARKER_CFG.copy()
    frame_marker_cfg.markers["frame"].scale = (0.1, 0.1, 0.1)
    ee_marker = VisualizationMarkers(frame_marker_cfg.replace(prim_path="/Visuals/ee_current"))
    goal_marker = VisualizationMarkers(frame_marker_cfg.replace(prim_path="/Visuals/ee_goal"))

    # Define goals for the arm
    ee_goals = [
        [0.3, 0., 0.2, 0, 1, 0, 0, 0], # [pos, rot, gripper_stat] # traj 1 - stiff 400, damp 80
        # [0.5, 0, 0.07, 0, 1, 0, 0, 0]
    ]
    ee_goals = torch.tensor(ee_goals, device=sim.device)
    current_goal_idx = 0

    ik_commands = torch.zeros(scene.num_envs, diff_ik_controller.action_dim, device=robot.device)
    print("ik action dim:, ", diff_ik_controller.action_dim)
    ik_commands[:] = ee_goals[current_goal_idx][:-1]

    robot_entity_cfg = SceneEntityCfg("robot", joint_names=["joint.*"], body_names=["xarm_gripper_base_link"])
    robot_entity_cfg.resolve(scene)

    ee_jacobi_idx = robot_entity_cfg.body_ids[0]-1

    lift_up_idx = 1000

    # Define simulation stepping
    sim_dt = sim.get_physics_dt()
    count = 0
    # Simulation loop
    joint_pos_init = robot.data.default_joint_pos.clone()
    joint_vel_init = robot.data.default_joint_vel.clone()
    while simulation_app.is_running():        
        # reset
        if count == 0:
            # reset joint state
            robot.write_joint_state_to_sim(joint_pos_init, joint_vel_init)
            robot.reset()
            print("done resetting")

            # joint_pos_des[:,7:] += 0.01
            ik_commands[:] = ee_goals[current_goal_idx][:-1]
            joint_pos_des = joint_pos_init[:, robot_entity_cfg.joint_ids].clone()

            # reset controller
            diff_ik_controller.reset()
            diff_ik_controller.set_command(ik_commands)

        else:
            jacobian = robot.root_physx_view.get_jacobians()[:, ee_jacobi_idx, :, robot_entity_cfg.joint_ids]
            ee_pose_w = robot.data.body_state_w[:, robot_entity_cfg.body_ids[0], 0:7]
            root_pose_w = robot.data.root_state_w[:, 0:7]
            joint_pos = robot.data.joint_pos[:, robot_entity_cfg.joint_ids]
            # compute frame in root frame
            ee_pos_b, ee_quat_b = subtract_frame_transforms(
                root_pose_w[:, 0:3], root_pose_w[:, 3:7], ee_pose_w[:, 0:3], ee_pose_w[:, 3:7]
            )
            # compute the joint commands
            joint_pos_des = diff_ik_controller.compute(ee_pos_b, ee_quat_b, jacobian, joint_pos)
            # import pdb
            # pdb.set_trace()
        # apply actions
        robot.set_joint_position_target(joint_pos_des, joint_ids=robot_entity_cfg.joint_ids)
        print("joint pos des: ", joint_pos_des)

        # if ee_goals[current_goal_idx][-1] == 0:
        #     # import pdb
        #     # pdb.set_trace()
        #     finger_joints_des = torch.tensor([[0.3, 0.3]], device='cuda')
        #     robot.set_joint_position_target(finger_joints_des, joint_ids=[8,11])
        #     if count >= lift_up_idx:
        #         finger_joints_des = torch.tensor([[0.01, 0.01]], device='cuda')
        #         robot.set_joint_position_target(finger_joints_des, joint_ids=[8,11])
        #     if count == lift_up_idx+10:
        #         ee_goals[current_goal_idx][2] += 0.25 # TODO: figure out why always below goal with gravity??
        #         ik_commands[:] = ee_goals[current_goal_idx][:-1]
        #         diff_ik_controller.set_command(ik_commands)
        
        # update sim
        scene.write_data_to_sim()
        # perform step
        sim.step()
        # update sim-time
        count += 1
        # update buffers
        scene.update(sim_dt)

        # obtain quantities from simulation
        ee_pose_w = robot.data.body_state_w[:, robot_entity_cfg.body_ids[0], 0:7]
        # update marker positions
        ee_marker.visualize(ee_pose_w[:, 0:3], ee_pose_w[:, 3:7])
        goal_marker.visualize(ik_commands[:, 0:3] + scene.env_origins, ik_commands[:, 3:7])

def main():
    """Main function."""
    # Load kit helper
    sim_cfg = sim_utils.SimulationCfg(
        dt=1 / 120,
        render_interval=2,
        disable_contact_processing=True,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=1.0,
            restitution=0.0,
        ),
    )
    sim = sim_utils.SimulationContext(sim_cfg)
    # Set main camera
    sim.set_camera_view([2.5, 2.5, 2.5], [0, 0, 0])
    # Design scene
    scene_cfg = FrankaCabinetScene(num_envs=args_cli.num_envs, env_spacing=3.0, replicate_physics=True)
    scene = InteractiveScene(scene_cfg)
    # Play the simulator
    sim.reset()
    # Now we are ready!
    print("[INFO]: Setup complete...")
    # Run the simulator
    run_simulator(sim, scene)


if __name__ == "__main__":
    # run the main function
    main()
    # close sim app
    simulation_app.close()

# def run_simulator(sim: sim_utils.SimulationContext, scene: InteractiveScene):
#     """Runs the simulation loop."""
#     # Extract scene entities
#     # note: we only do this here for readability.
#     robot = scene["robot"]

#     # Create controller
#     diff_ik_cfg = DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method="dls")
#     diff_ik_controller = DifferentialIKController(diff_ik_cfg, num_envs=scene.num_envs, device=sim.device)

#     # Markers
#     frame_marker_cfg = FRAME_MARKER_CFG.copy()
#     frame_marker_cfg.markers["frame"].scale = (0.1, 0.1, 0.1)
#     ee_marker = VisualizationMarkers(frame_marker_cfg.replace(prim_path="/Visuals/ee_current"))
#     goal_marker = VisualizationMarkers(frame_marker_cfg.replace(prim_path="/Visuals/ee_goal"))

#     # Define goals for the arm
#     ee_goals = [
#         # [0.5, 0., 0.13, 0, 0.707, 0.707, 0, 0], # [pos, rot, gripper_stat] # traj 1 - stiff 400, damp 80
#         [0.5, 0, 0.07, 0, 1, 0, 0, 0]
#     ]
#     ee_goals = torch.tensor(ee_goals, device=sim.device)
#     # Track the given command
#     current_goal_idx = 0
#     # Create buffers to store actions
#     ik_commands = torch.zeros(scene.num_envs, diff_ik_controller.action_dim, device=robot.device)
#     print("ik action dim:, ", diff_ik_controller.action_dim)
#     ik_commands[:] = ee_goals[current_goal_idx][:-1]

#     # Specify robot-specific parameters
#     if args_cli.robot == "franka_panda":
#         robot_entity_cfg = SceneEntityCfg("robot", joint_names=[".*"], body_names = ["left.*", "right.*"])
#     # Resolving the scene entities
#     robot_entity_cfg.resolve(scene)

#     # Obtain the frame index of the end-effector
#     # For a fixed base robot, the frame index is one less than the body index. This is because
#     # the root body is not included in the returned Jacobians.
#     if robot.is_fixed_base:
#         ee_jacobi_idx = robot_entity_cfg.body_ids[0] - 1
#     else:
#         ee_jacobi_idx = robot_entity_cfg.body_ids[0]

#     # Define simulation stepping
#     sim_dt = sim.get_physics_dt()
#     count = 0
#     # Simulation loop
#     while simulation_app.is_running():
#         # reset
#         if count == 0:
#             # reset joint state
#             joint_pos = robot.data.default_joint_pos.clone()
#             joint_vel = robot.data.default_joint_vel.clone()
#             robot.write_joint_state_to_sim(joint_pos, joint_vel)
#             robot.reset()
#             # reset actions
#             ik_commands[:] = ee_goals[current_goal_idx][:-1]
#             joint_pos_des = joint_pos[:, robot_entity_cfg.joint_ids].clone()
#             # reset controller
#             diff_ik_controller.reset()
#             diff_ik_controller.set_command(ik_commands)
#             # # change goal
#             # current_goal_idx = (current_goal_idx + 1) % len(ee_goals)
#         else:
#             # obtain quantities from simulation
#             jacobian = robot.root_physx_view.get_jacobians()[:, ee_jacobi_idx, :, robot_entity_cfg.joint_ids]
#             ee_pose_w = robot.data.body_state_w[:, robot_entity_cfg.body_ids[0], 0:7]

#             root_pose_w = robot.data.root_state_w[:, 0:7]
#             joint_pos = robot.data.joint_pos[:, robot_entity_cfg.joint_ids]
#             # compute frame in root frame
#             ee_pos_b, ee_quat_b = subtract_frame_transforms(
#                 root_pose_w[:, 0:3], root_pose_w[:, 3:7], ee_pose_w[:, 0:3], ee_pose_w[:, 3:7]
#             )
#             # compute the joint commands
#             joint_pos_des = diff_ik_controller.compute(ee_pos_b, ee_quat_b, jacobian, joint_pos)


#         # apply actions
#         robot.set_joint_position_target(joint_pos_des, joint_ids=robot_entity_cfg.joint_ids)
#         scene.write_data_to_sim()
#         # perform step
#         sim.step()
#         # update sim-time
#         count += 1
#         # update buffers
#         scene.update(sim_dt)

#         # obtain quantities from simulation
#         ee_pose_w = robot.data.body_state_w[:, robot_entity_cfg.body_ids[0], 0:7]
#         # update marker positions
#         ee_marker.visualize(ee_pose_w[:, 0:3], ee_pose_w[:, 3:7])
#         goal_marker.visualize(ik_commands[:, 0:3] + scene.env_origins, ik_commands[:, 3:7])

#     # current_dir = os.path.dirname(os.path.abspath(__file__))
#     # output_path = os.path.join(current_dir, "wp_traj1","wp_traj_cube_expert2.txt")
#     # with open(output_path, "w") as file:
#     #     for elt in wp_traj:
#     #         file.write(f"{elt}\n")

# def main():
#     """Main function."""
#     # Load kit helper
#     sim_cfg = sim_utils.SimulationCfg(
#         dt=1 / 120,
#         render_interval=2,
#         disable_contact_processing=True,
#         physics_material=sim_utils.RigidBodyMaterialCfg(
#             friction_combine_mode="multiply",
#             restitution_combine_mode="multiply",
#             static_friction=1.0,
#             dynamic_friction=1.0,
#             restitution=0.0,
#         ),
#     )
#     sim = sim_utils.SimulationContext(sim_cfg)
#     # Set main camera
#     sim.set_camera_view([2.5, 2.5, 2.5], [0, 0, 0])
#     # Design scene
#     scene_cfg = FrankaCabinetScene(num_envs=args_cli.num_envs, env_spacing=3.0, replicate_physics=True)
#     scene = InteractiveScene(scene_cfg)
#     # Play the simulator
#     sim.reset()
#     # Now we are ready!
#     print("[INFO]: Setup complete...")
#     # Run the simulator
#     run_simulator(sim, scene)


# if __name__ == "__main__":
#     # run the main function
#     main()
#     # close sim app
#     simulation_app.close()