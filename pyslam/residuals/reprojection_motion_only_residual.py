import numpy as np
from liegroups import SE3
from pyslam.utils import stackmul
from numba import guvectorize, float64
se3_odot_shape = np.empty(6)


@guvectorize([(float64[:], float64[:], float64[:, :])],
             '(n),(m)->(n,m)', nopython=True, cache=True, target='parallel')
def fast_se3_odot(vec, junk, out):
    out[0, 0] = 1.
    out[0, 1] = 0.
    out[0, 2] = 0.
    out[0, 3] = 0.
    out[0, 4] = vec[2]
    out[0, 5] = -vec[1]
    out[1, 0] = 0.
    out[1, 1] = 1.
    out[1, 2] = 0.
    out[1, 3] = -vec[2]
    out[1, 4] = 0.
    out[1, 5] = vec[0]
    out[2, 0] = 0.
    out[2, 1] = 0.
    out[2, 2] = 1.
    out[2, 3] = vec[1]
    out[2, 4] = -vec[0]
    out[2, 5] = 0.

# @guvectorize([(float64[:], float64[:], float64[:, :])],
#              '(n),(m)->(n,m)', nopython=True, cache=True, target='parallel')
# def fast_stiffness_reweight(stiffness, reweight, out):


class ReprojectionMotionOnlyResidual:
    """Frame to frame reprojection error for any kind of camera."""

    def __init__(self, camera, obs_1, obs_2, stiffness):
        self.camera = camera
        self.obs_1 = obs_1
        self.obs_2 = obs_2
        self.stiffness = stiffness

    def evaluate(self, params, compute_jacobians=None):
        """ This is my docstring. """
        T_cam2_cam1 = params[0]
        pt_cam1 = self.camera.triangulate(self.obs_1)
        pt_cam2 = T_cam2_cam1.dot(pt_cam1)

        if compute_jacobians:
            jacobians = [None for _ in enumerate(params)]

            predicted_obs, cam_jacobian = self.camera.project(
                pt_cam2, compute_jacobians=True)

            residual = np.dot(self.stiffness, predicted_obs - self.obs_2)

            if compute_jacobians[0]:
                jacobians[0] = np.dot(self.stiffness,
                                      cam_jacobian.dot(SE3.odot(pt_cam2)))

            return residual, jacobians

        residual = np.dot(self.stiffness,
                          self.camera.project(pt_cam2) - self.obs_2)
        return residual


class ReprojectionMotionOnlyBatchResidual:
    """Frame to frame reprojection error with batch jacobians (for multiple reprojections)."""

    def __init__(self, camera, obs_1, obs_2, stiffness):
        self.camera = camera
        self.obs_1 = obs_1
        self.obs_2 = obs_2
        self.stiffness = stiffness
        self.pt_cam1 = self.camera.triangulate(self.obs_1)
        self.num_pts = len(self.obs_1)

    def evaluate(self, params, compute_jacobians=None):
        """ """
        T_cam2_cam1 = params[0]
        pt_cam2 = T_cam2_cam1.dot(self.pt_cam1)

        if compute_jacobians:
            jacobians = [None for _ in enumerate(params)]

            predicted_obs, cam_jacobians = self.camera.project(
                pt_cam2, compute_jacobians=True)

            residual = np.reshape(
                self.stiffness.dot((predicted_obs - self.obs_2).T),
                3 * self.num_pts, order='F')

            if compute_jacobians[0]:
                odot_pt_cam2 = fast_se3_odot(pt_cam2, se3_odot_shape)
                inner_jacob = stackmul(cam_jacobians, odot_pt_cam2)

                # We must multiply all the Jacobians by a stiffness matrix
                # Repeat the stiffness matrix (3,3) into a (N,3,3) matrix
                stiffness_repeats = np.asarray([self.stiffness] * self.num_pts)

                # Optional: reweight stiffness according to KITTI heuristic
                reweights = 1 / \
                    np.sqrt(
                        np.abs(self.obs_2[:, 0] - self.camera.cu) / self.camera.cu + 0.05)
                stiffness_repeats = np.multiply(
                    stiffness_repeats, np.reshape(reweights, [self.num_pts, 1, 1]))

                jacob = stackmul(stiffness_repeats, inner_jacob)

                # Reshape back into a (3*N, 6) Jacobian
                jacobians[0] = np.reshape(jacob, [3 * self.num_pts, 6])

            return residual, jacobians

        # Multiply (3,3) by (3,N), and then reshape to get a (3*N,) array
        residual = np.reshape(
            self.stiffness.dot((self.camera.project(pt_cam2) - self.obs_2).T),
            3 * self.num_pts, order='F')

        return residual


class ReprojectionTranslationOnlyBatchResidual:
    """Frame to frame reprojection error with batch jacobians and (for multiple reprojections) for translation only."""

    def __init__(self, camera, obs_1, obs_2, C_21, stiffness):
        self.camera = camera
        self.obs_1 = obs_1
        self.obs_2 = obs_2
        self.C_21 = C_21
        self.stiffness = stiffness
        self.pt_cam1 = self.camera.triangulate(self.obs_1)
        self.num_pts = len(self.obs_1)

    def evaluate(self, params, compute_jacobians=None):
        """ """
       # t_21_1 = params[0]
       # T_cam2_cam1 = SE3(rot=self.C_21, trans=-1*self.C_21.as_matrix().dot(t_21_1))
        t_12_2 = params[0]
        T_cam2_cam1 = SE3(rot=self.C_21, trans=t_12_2)
        pt_cam2 = T_cam2_cam1.dot(self.pt_cam1)

        if compute_jacobians:
            jacobians = [None for _ in enumerate(params)]

            predicted_obs, cam_jacobians = self.camera.project(
                pt_cam2, compute_jacobians=True)

            residual = np.reshape(
                self.stiffness.dot((predicted_obs - self.obs_2).T),
                3 * self.num_pts, order='F')

            if compute_jacobians[0]:
                # C_21_repeats = np.asarray([-1*self.C_21.as_matrix()]*self.num_pts) #Repeat the jacobian matrix (3,3) into a (N,3,3) matrix
                #inner_jacob = stackmul(cam_jacobians, C_21_repeats)
                inner_jacob = cam_jacobians
                # We must multiply all the Jacobians by a stiffness matrix
                # Repeat the stiffness matrix (3,3) into a (N,3,3) matrix
                stiffness_repeats = np.asarray([self.stiffness] * self.num_pts)
                jacob = stackmul(stiffness_repeats, inner_jacob)

                # Reshape back into a (3*N, 3) Jacobian
                jacobians[0] = np.reshape(jacob, [3 * self.num_pts, 3])

            return residual, jacobians

        # Multiply (3,3) by (3,N), and then reshape to get a (3*N,) array
        residual = np.reshape(
            self.stiffness.dot((self.camera.project(pt_cam2) - self.obs_2).T),
            3 * self.num_pts, order='F')

        return residual
