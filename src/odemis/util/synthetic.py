# -*- encoding: utf-8 -*-
"""
synthetic.py : To obtain the characteristic size of the point spread function
(PSF) of a microscope system, and to generate simulated images containing one
or multiple spots (PSF's).

Copyright (C) 2021  Andries Effting, Delmic

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
USA.

"""
import logging
import math
from typing import List, Tuple, Union

import numpy

from odemis import model

Shape2D = Tuple[int, int]
Coordinate = Tuple[float, float]
CoordinateList = List[Coordinate]


UINT16_MAX = numpy.iinfo(numpy.uint16).max


def psf_sigma_wffm(
    refractive_index: float, numerical_aperture: float, wavelength: float
) -> float:
    """
    Calculate the Gaussian approximation of a wide field fluorescence
    microscope point spread function.

    Parameters
    ----------
    refractive_index : float, >= 1
        Refractive index
    numerical_aperture: float, positive
        Numerical aperture of the optical system
    wavelength : float
        Wavelength.

    Returns
    -------
    sigma : float
        The standard deviation of the Gaussian approximation of a fluorescence
        microscope point spread function. Same units as `wavelength`.

    References
    ----------
    .. [1] Zhang, B., Zerubia, J., & Olivo-Marin, J. C. (2007). Gaussian
    approximations of fluorescence microscope point-spread function models.
    Applied optics, 46(10), 1819-1829.

    """
    if refractive_index < 1:
        raise ValueError("The refractive index should be greater than or equal to 1.")
    if numerical_aperture <= 0:
        raise ValueError("The numerical aperture should be positive.")
    if wavelength <= 0:
        raise ValueError("The wavelength should be positive.")
    if numerical_aperture >= refractive_index:
        raise ValueError(
            "The numerical aperture should be less than the refractive index."
        )

    k = 2 * math.pi / wavelength
    nk = refractive_index * k
    sa = numerical_aperture / refractive_index
    ca = math.sqrt(1 - sa ** 2)
    t = ca ** 1.5
    sigma = 1 / (nk * math.sqrt((4 - 7 * t + 3 * ca ** 3.5) / (7 * (1 - t))))
    return sigma


def psf_gaussian(
    shape: Shape2D, loc: Union[Coordinate, CoordinateList], sigma: float
) -> numpy.ndarray:
    """
    Return a synthetic spot image of a point-spread function (PSF) approximated
    by a 2-dimensional Gaussian function.

    Parameters
    ----------
    shape : tuple of ints
        Shape of the array, e.g. ``(9, 9)``.
    loc : tuple of floats, or list of tuple of floats
        Position of the maximum in pixel coordinates `(j0, i0)` relative to the
        center of the spot image.
    sigma : float, positive
        Standard deviation of the Gaussian.

    Returns
    -------
    image : ndarray, dtype=numpy.uint16
        Array with the image of the point spread function with the given shape
        and size and at the given location.

    """
    if sigma <= 0:
        raise ValueError("sigma should be positive")

    n, m = shape
    j = numpy.arange(n, dtype=numpy.float64)
    i = numpy.arange(m, dtype=numpy.float64)
    out = numpy.zeros((n, m), dtype=numpy.float64)
    for j0, i0 in numpy.atleast_2d(loc):
        kj = numpy.exp(-0.5 * numpy.square((j - j0) / sigma))
        ki = numpy.exp(-0.5 * numpy.square((i - i0) / sigma))
        out += numpy.outer(kj, ki)

    # convert to uint16
    numpy.clip(out, 0, 1, out=out)
    numpy.rint(UINT16_MAX * out, out=out)
    return out.astype(numpy.uint16)


class ParabolicMirrorRayTracer:
    """
    Simulates ray tracing for a parabolic mirror system with a lens and camera.

    This class provides methods to generate and trace rays from a hemispherical source,
    compute their intersection with a paraboloid, reflect them, refract them through a lens,
    and finally map their positions on a camera plane. Useful for simulating optical systems
    involving parabolic mirrors and lenses (SPARC2).

    Source:
        - Coenen, T. (2014). Angle-resolved cathodoluminescence nanoscopy. [Thesis, Universiteit van Amsterdam]
        - T. Coenen and A. Polman, "Polarization-sensitive cathodoluminescence Fourier microscopy," Opt. Express  20, 18679-18691 (2012).
        - Hecht, E. (2002). Optics. Addison Wesley.
    """

    def __init__(
        self,
        good_pos: dict,
        x_max: float = 13.25e-3,
        hole_diam: float = 0.6e-3,
        focus_dist: float = 0.5e-3,
        parabola_f: float = 2.5e-3,
        res: tuple = (1024, 256),
        pixel_size: tuple = (26e-6, 26e-6),
        det_y_offset: float = 0.0,
        det_z_offset: float = 2.0e-3,
    ):
        """
        Initialize the ParabolicMirrorRayTracer with optical geometry and defaults.

        Sets up the parabolic mirror, lens and camera geometry, detector sampling and
        internal cache used to avoid re-simulating identical positions.

        :param good_pos: Reference/aligned component positions with keys
                         'x', 'y', 'z' [m].
        :param x_max: Distance between the parabola origin and the cutoff position [m].
        :param hole_diam: Diameter of the hole in the mirror [m].
        :param focus_dist: The vertical mirror cutoff, iow the min distance between the mirror and the sample [m].
        :param parabola_f: parabola_parameter=1/(4f); f: focal point of mirror (place of sample) [m].
        :param res: Resolution of the detector (z_pixels, y_pixels).
        :param pixel_size: Pixel size of the detector [m].
        :param det_y_offset: Offset of the detector center in the y direction [m].
        :param det_z_offset: Offset of the detector center in the z direction [m].
        :raises: (ValueError) if good_pos doesn't contain 'x', 'y', 'z' keys.
        """
        # Validate good_pos structure
        required_keys = {'x', 'y', 'z'}
        if not required_keys.issubset(good_pos.keys()):
            missing = required_keys - good_pos.keys()
            raise ValueError(f"good_pos must contain keys {required_keys}, missing: {missing}")

        self.a = 1 / (4 * parabola_f)
        self.x_cut = x_max - parabola_f
        self.focus_dist = focus_dist
        self.hole_diam = hole_diam

        # Position of lens and camera with respect to origin (x=0).
        # Camera distance should be lens_distance plus focal length lens to be in focus
        # To simulate AR mode the camera can be placed behind the focal point
        self.lens_distance = 308e-3
        self.focl = 200e-3
        # Spectroscopy mode
        self.camera_distance = self.lens_distance + self.focl
        self.nrays = 100_000
        # AR
        self.lensc = (0, 2.8e-3)  # position of the lens center in the (y,z) plane

        self.resolution = model.TupleVA(res)
        self.pixel_size = model.TupleVA(pixel_size)

        # Convert detector properties to physical dimensions
        y_width = self.resolution.value[0] * self.pixel_size.value[0]
        self.y_range = (det_y_offset - y_width / 2, det_y_offset + y_width / 2)
        self.det_y_offset = det_y_offset
        z_width = self.resolution.value[1] * self.pixel_size.value[1]
        self.z_range = (det_z_offset - z_width / 2, det_z_offset + z_width / 2)
        self.det_z_offset = det_z_offset

        self.alpha = numpy.deg2rad(136)

        self._aligned_pos = self._last_pos = good_pos
        self._last_img = self._get_ray_traced_pattern()
        self.resolution.subscribe(self._update_range)
        self.pixel_size.subscribe(self._update_range)

    def _update_range(self, _):
        """Update the detector range based on resolution and pixel size changes."""
        y_width = self.resolution.value[0] * self.pixel_size.value[0]
        self.y_range = (self.det_y_offset - y_width / 2, self.det_y_offset + y_width / 2)
        z_width = self.resolution.value[1] * self.pixel_size.value[1]
        self.z_range = (self.det_z_offset - z_width / 2, self.det_z_offset + z_width / 2)

    def _spherical_source(self, source_pos, npoints=1000, sequence="equidis"):
        """
        Defines a hemispherical point source at a specific position in space.

        :param source_pos: (list of float) [x, y, z] coordinates of the source position.
        :param npoints: (int) Number of points/rays to generate.
        :param sequence: (str) Sequence type for point distribution ("equidis" or "fibonacci").
        :return: (tuple) (rays, thetalist, philist) where rays is an array of ray vectors,
                 thetalist and philist are arrays of spherical angles.
        """
        npoints = int(npoints)
        if sequence == "fibonacci":
            goldenratio = (1 + 5**0.5) / 2
            i = numpy.arange(0, npoints // 2)
            phi = 2 * numpy.pi * i / goldenratio
            theta = numpy.arccos(1 - 2 * (i + 0.5) / npoints)
            xd, yd, zd = (
                numpy.sin(theta) * numpy.cos(phi),
                numpy.sin(theta) * numpy.sin(phi),
                numpy.cos(theta),
            )
        elif sequence == "equidis":
            r = 1
            a = 2 * numpy.pi * r**2 / npoints
            d = numpy.sqrt(a)
            mtheta = int(numpy.round(numpy.pi / (d * 2)))
            dtheta = numpy.pi / (mtheta * 2)
            dphi = a / dtheta

            thetalist = []
            philist = []

            for m in range(0, mtheta):
                theta = 0.5 * numpy.pi * (m + 0.5) / mtheta
                mphi = int(numpy.round(2 * numpy.pi * numpy.sin(theta) / dphi))
                for n in range(0, mphi):
                    phi = 2 * numpy.pi * n / mphi
                    thetalist.append(theta)
                    philist.append(phi)

            thetalist = numpy.array(thetalist)
            philist = numpy.array(philist)
            xd, yd, zd = (
                numpy.sin(thetalist) * numpy.cos(philist),
                numpy.sin(thetalist) * numpy.sin(philist),
                numpy.cos(thetalist),
            )
            theta = thetalist
            phi = philist
        else:
            raise ValueError(f"Unknown sequence type: {sequence}")

        npoints1 = xd.size
        x = numpy.ones([npoints1]) * source_pos[0]
        y = numpy.ones([npoints1]) * source_pos[1]
        z = numpy.ones([npoints1]) * source_pos[2]
        rays = numpy.transpose(numpy.vstack((xd, yd, zd, x, y, z)))

        return rays, theta, phi

    def _intersect_parabola(self, ray_vecs, a, xcut, dfoc, holediam):
        """
        Computes intersection points of rays with a 3D paraboloid.

        :param ray_vecs: (ndarray) Array of ray vectors.
        :param a: (float) Paraboloid parameter.
        :param xcut: (float) X cutoff for the paraboloid.
        :param dfoc: (float) Focal plane offset.
        :param holediam: (float) Diameter of the central hole.
        :return: (tuple) (ppos_corrected, rays_corrected, mask) where ppos_corrected are intersection points,
                 rays_corrected are the corresponding ray vectors, and mask is a boolean mask array.

        We set the parametric line equations equal to the 3D paraboloid equation to
        find the intersection points. See arithmetic below. Because the paraboloid
        is described by a parabolic equation the intersection point equation is a
        second order polynomial which has two standard solutions because the line
        can intersect the paraboloid twice. eq = a*y0**2+a*(c2*t)**2+2*a*c2*y0*t+a*z0**2+a*(c3*t)**2
        +2*a*c3*z0*t-1/(4*a)-x0-c1t  eq1 = a*(c2**2+c3**2)*t**2+a*(2*c2*y0+2*c3*z0
        - c1/a)*t+a*(y0**2+z0**2)-1/(4*a)-x0, eq2 = coeff1*t**2+coeff2*t+constant = 0
        """

        c1 = ray_vecs[:, 0]
        c2 = ray_vecs[:, 1]
        c3 = ray_vecs[:, 2]
        x0 = ray_vecs[:, 3]
        y0 = ray_vecs[:, 4]
        z0 = ray_vecs[:, 5]

        # for 0 pitch and 0 yaw the solution is simpler
        if numpy.allclose(c2[~numpy.isnan(c2)], 0) and numpy.allclose(c3[~numpy.isnan(c3)], 0):
            r = numpy.sqrt(y0**2 + z0**2)
            x1 = a * r**2 - 1 / (4 * a)
            y1 = y0
            z1 = z0
        else:
            coeff1 = a * (c2**2 + c3**2)
            coeff2 = a * (2 * c2 * y0 + 2 * c3 * z0 - c1 / a)
            constant = a * (y0**2 + z0**2) - 1 / (4 * a) - x0

            # First solution is on the wrong side of paraboloid and intersects at large x so we only need solution2
            solution1 = (-coeff2 + numpy.sqrt(coeff2**2 - 4 * coeff1 * constant)) / (2 * coeff1)
            t = solution1

            x1 = x0 + c1 * t
            y1 = y0 + c2 * t
            z1 = z0 + c3 * t

        ppos = numpy.transpose(numpy.vstack((x1, y1, z1)))

        # Radius in the plane transverse to the optical axis (y–z plane)
        r_inplane = numpy.sqrt(y1**2 + z1**2)

        mask = numpy.ones(numpy.shape(x1))
        mask[(x1 > xcut) | (z1 < dfoc) | (r_inplane < holediam / 2)] = numpy.nan

        # remove rays that fall outside of mirror. There may be simpler ways to do this
        ppos_msize = mask[~numpy.isnan(mask)].size
        ppos_corrected = numpy.empty([ppos_msize, 3])
        rays_corrected = numpy.empty([ppos_msize, 6])

        for i in range(0, 3):
            ppos_el = ppos[:, i]
            ppos_corrected[:, i] = ppos_el[~numpy.isnan(mask)]

        for j in range(0, 6):
            rays_el = ray_vecs[:, j]
            rays_corrected[:, j] = rays_el[~numpy.isnan(mask)]

        return ppos_corrected, rays_corrected, mask

    def _matrix_dot(self, a, b):
        """
        Performs a parallel dot product for arrays of vectors.

        :param a: (ndarray) Array of vectors.
        :param b: (ndarray) Array of vectors.
        :return: (ndarray) Array of dot products for each vector pair.
        """
        dotmat = numpy.sum(a.conj() * b, 1)

        return dotmat

    def _normalize_vec_p(self, vector_in):
        """
        Normalizes an array of vectors in parallel.

        Function that performs a parallel normalization for an array with size
        [x, n] with x being the number of rays and n being the number of dimensions
        in the vector (usually 3 in this case)

        :param vector_in: (ndarray) Array of vectors to normalize.
        :return: (ndarray) Array of normalized vectors.
        """
        dotm = numpy.sqrt(self._matrix_dot(vector_in, vector_in))
        dotm1 = numpy.transpose(numpy.tile(dotm, (3, 1)))
        vector_out = vector_in / dotm1
        # remove NaN's from array, which come from the hard coded zeros in the CL data
        vector_out[numpy.isnan(vector_out)] = 0

        return vector_out

    def _parabola_normal_p(self, ppos, a):
        """
        Calculates the surface normal vectors of a paraboloid at given positions.
        It calculates vectorial parabola normal in parallel for incoming rays.

        :param ppos: (ndarray) Array of intersection points on the paraboloid.
        :param a: (float) Paraboloid parameter.
        :return: (ndarray) Array of normal vectors at each position.
        """
        # definitions of r and theta for the parabola.
        r = numpy.sqrt(ppos[:, 1] ** 2 + ppos[:, 2] ** 2)
        theta1 = numpy.arccos(ppos[:, 1] / r)

        # To calculate the surface normal we calculate the gradient of the radius and of
        # theta by symbolic differentiation of the parabola formula.
        # The parabola formula looks as follows r=[a*r^2 r*cos(theta1)  r*sin(theta1)].
        # We pick x to be along the optical axis of the parabola and  y transverse
        # to it. Z is the dimension perpendicular to the sample

        gradr = numpy.vstack(((2 * a * r), (numpy.cos(theta1)), (numpy.sin(theta1))))
        gradtheta = numpy.vstack(
            (
                (numpy.zeros(numpy.shape(r))),
                (-r * numpy.sin(theta1)),
                (r * numpy.cos(theta1)),
            )
        )

        # compute surface normal from cross product of gradients
        normal = numpy.transpose(numpy.cross(gradr, gradtheta, axis=0))
        normal = self._normalize_vec_p(normal)

        return normal

    def _em_dir_2d(self, normal, emin):
        """
        Calculates the emission direction after reflection based on the surface normal and incoming direction.

        :param normal: (ndarray) Array of surface normal vectors.
        :param emin: (ndarray) Array of incoming emission direction vectors.
        :return: (ndarray) Array of reflected direction vectors.
        """
        refl = -(
            2 * numpy.transpose(numpy.tile(self._matrix_dot(normal, emin), (3, 1))) * normal - emin
        )
        return refl

    def _camera_plane_rays(self, cam_x, ppos, refl):
        """
        Computes the intersection points of rays with a camera plane at a given x position.
        Plot rays for a particular x value. Function makes use of parametric line
        formulas x = x0 + t * xprime where t = (z - z0) / zprime where zprime is the
        directional unit vector.

        :param cam_x: (float) X position of the camera plane.
        :param ppos: (ndarray) Array of starting positions of rays.
        :param refl: (ndarray) Array of direction vectors of rays.
        :return: (ndarray) Array of intersection points on the camera plane.
        """
        # parametric factor
        t = (cam_x - ppos[:, 0]) / refl[:, 0]
        t3 = numpy.transpose(numpy.tile(t, (3, 1)))
        rays = ppos + t3 * refl

        return rays

    def _hit_lens(
        self,
        vector_in,
        pos_in,
        n1=1,
        n2=1.458461,
        lens_diam=50e-3,
        focal_length=200e-3,
        lens_center=(0, 2.8e-3),
        lens_crop=False,
    ):
        """
        Calculates ray refraction through a plano-convex lens. In this case a plano convex lens
        is used. The focal length, lens diameter, refractive index and lens center
        in the yz plane can be chosen

        :param vector_in: (ndarray) Array of incoming ray direction vectors.
        :param pos_in: (ndarray) Array of incoming ray positions.
        :param n1: (float) Refractive index of the initial medium.
        :param n2: (float) Refractive index of the lens.
        :param lens_diam: (float) Diameter of the lens.
        :param focal_length: (float) Focal length of the lens.
        :param lens_center: (list of float) [y, z] coordinates of the lens center.
        :param lens_crop: (bool) Whether to crop rays outside the lens diameter.
        :return: (tuple) (refracted_raysr2_cor, pos_in_cor) where refracted_raysr2_cor are refracted ray directions,
                 pos_in_cor are corresponding positions.
        """
        lensr = focal_length * (n2 - 1)
        # this gives R = 91.69 consistent with value given by Edmund.
        # n2 value chosen for fused silica index @587.725 nm

        yin = pos_in[:, 1] - lens_center[0]
        zin = pos_in[:, 2] - lens_center[1]
        rin = numpy.sqrt(yin**2 + zin**2)

        theta = numpy.arccos(zin / lensr)
        phi = -(numpy.arcsin(yin / (lensr * numpy.sin(theta))) + numpy.pi)

        # gradients spherical angles
        normalr1 = numpy.transpose(
            numpy.vstack(
                (
                    numpy.sin(theta) * numpy.cos(phi),
                    numpy.sin(theta) * numpy.sin(phi),
                    numpy.cos(theta),
                )
            )
        )
        # Assuming a plano-convec lens the surface normal will be the same for all incoming rays
        normalr2 = numpy.transpose(
            numpy.vstack(
                (
                    -numpy.ones(normalr1.shape[0]),
                    numpy.zeros(normalr1.shape[0]),
                    numpy.zeros(normalr1.shape[0]),
                )
            )
        )

        rr = n1 / n2
        cc = numpy.transpose(numpy.tile(self._matrix_dot(-normalr1, vector_in), (3, 1)))

        # vectorial notation of snell's law, see Wiki on snell's law
        refracted_raysr1 = self._normalize_vec_p(
            rr * vector_in + (rr * cc - numpy.sqrt(1 - (1 - cc**2) * rr**2)) * normalr1
        )

        rr1 = n2 / n1
        cc1 = numpy.transpose(
            numpy.tile(self._matrix_dot(-normalr2, refracted_raysr1), (3, 1))
        )

        # This is using thin lens approximation where both interfaces are in the same plane.
        # We can also account for propagation within lens
        refracted_raysr2 = self._normalize_vec_p(
            rr1 * refracted_raysr1
            + (rr1 * cc1 - numpy.sqrt(1 - (1 - cc1**2) * rr1**2)) * normalr2
        )

        if lens_crop is True:
            mask = numpy.ones(numpy.shape(yin))
            mask[rin > lens_diam / 2] = numpy.nan

            # remove rays that fall outside of mirror. There may be simpler ways to do this
            msize = mask[~numpy.isnan(mask)].size
            pos_in_cor = numpy.empty([msize, 3])
            refracted_raysr2_cor = numpy.empty([msize, 3])

            for i in range(0, 3):
                pos_in_el = pos_in[:, i]
                pos_in_cor[:, i] = pos_in_el[~numpy.isnan(mask)]
                refracted_rays_el = refracted_raysr2[:, i]
                refracted_raysr2_cor[:, i] = refracted_rays_el[~numpy.isnan(mask)]
        else:
            refracted_raysr2_cor = refracted_raysr2
            pos_in_cor = pos_in

        return refracted_raysr2_cor, pos_in_cor

    def _rays_yz_camera_mapping_grey(
        self, rays_cam, intensity, y_range, z_range, y_bins, z_bins
    ):
        """
        Plots a heatmap for a particular x-slice in the camera plane with fixed sampling.

        :param rays_cam: (ndarray) Array of ray positions at the camera.
        :param intensity: (ndarray) Array of ray intensities.
        :param y_range: (tuple) (min, max) range for y-axis in m.
        :param z_range: (tuple) (min, max) range for z-axis in m.
        :param y_bins: (int) Number of bins along y-axis.
        :param z_bins: (int) Number of bins along z-axis.
        :return: (ndarray) 2D array representing the intensity mapping on the camera.
        """

        yvals = rays_cam[:, 1]
        zvals = rays_cam[:, 2]
        mapping, _, _ = numpy.histogram2d(
            yvals,
            zvals,
            bins=[y_bins, z_bins],
            range=[y_range, z_range],
            weights=intensity,
        )
        mapping2d = numpy.flipud(mapping.T)

        numpy.clip(mapping2d, 0, numpy.iinfo(numpy.uint16).max, out=mapping2d)
        mapping2d = mapping2d.astype(numpy.uint16)

        return mapping2d

    def _get_ray_traced_pattern(self, dx=0.0, dy=0.0, dz=0.0) -> numpy.ndarray:
        """
        Get a ray-traced pattern based on the current misalignment.

        :param dx: (float) Misalignment in the x direction [m].
        :param dy: (float) Misalignment in the y direction [m].
        :param dz: (float) Misalignment in the z direction [m].
        :return: 2D array representing the simulated intensity pattern on the camera.
        """
        source_pos = [dx, dy, dz]
        ray_vecs_source, theta, _ = self._spherical_source(source_pos, self.nrays)

        # Find intersection points with paraboloid
        ppos_source, ray_vecs_source_c, raymask_source = self._intersect_parabola(
            ray_vecs_source, self.a, self.x_cut, self.focus_dist, self.hole_diam
        )
        # Compute parabola surface normal
        normal_source = self._parabola_normal_p(ppos_source, self.a)
        # Calculate ray vectors after reflection from paraboloid
        refl_source = self._em_dir_2d(normal_source, ray_vecs_source_c[:, 0:3])

        # Lambertian source in intensity, can also be interchanged for another distribution
        intensity_source = numpy.cos(theta[~numpy.isnan(raymask_source)])

        # Compute ray positions up to lens
        rays_before_lens = self._camera_plane_rays(
            self.lens_distance, ppos_source, refl_source
        )
        # Compute refraction from lens, currently there is still spherical abberation in the beam
        refracted_rays, rays_after_lens = self._hit_lens(
            refl_source,
            rays_before_lens,
            focal_length=self.focl,
            lens_center=self.lensc,
        )
        # Compute ray positions at camera position after lens
        rays_camera = self._camera_plane_rays(
            self.camera_distance, rays_after_lens, refracted_rays
        )
        # Map on a detector with DU920P dimensions
        mapping2d = self._rays_yz_camera_mapping_grey(
            rays_camera,
            intensity_source,
            y_range=self.y_range,
            z_range=self.z_range,
            y_bins=self.resolution.value[0],
            z_bins=self.resolution.value[1],
        )
        return mapping2d

    def simulate(self, current_pos: dict) -> numpy.ndarray:
        """
        Simulate a raytraced intensity pattern.

        :param current_pos: Current positions with keys 'x', 'y', 'z' (floats, in meters).
        :return: 2D array representing the simulated intensity pattern on the camera.
        """
        if self._last_pos != current_pos:
            try:
                logging.debug("Simulating new ray-traced image")
                dx = self._aligned_pos["x"] - current_pos["x"]
                dy = self._aligned_pos["y"] - current_pos["y"]
                dz = self._aligned_pos["z"] - current_pos["z"]
                with numpy.errstate(all="raise"):
                    self._last_img = self._get_ray_traced_pattern(dx, dy, dz)
                self._last_pos = current_pos.copy()
            except Exception as e:
                logging.warning(f"Ray tracing failed with error: {e}. Using last image.")

        return self._last_img.copy()
