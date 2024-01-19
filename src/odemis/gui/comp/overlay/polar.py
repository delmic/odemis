# -*- coding: utf-8 -*-

"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import cairo
import math
import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.util.conversion as conversion


class PolarOverlay(base.ViewOverlay):

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

        self.canvas_padding = 0
        # Rendering attributes
        self.center_x = None
        self.center_y = None
        self.radius = None
        self.inner_radius = None
        self.tau = 2 * math.pi
        self.num_ticks = 6
        self.ticks = []

        self.ticksize = 10

        # Value attributes
        self.px, self.py = None, None
        self.tx, self.ty = None, None

        self.colour = conversion.hex_to_frgb(gui.SELECTION_COLOUR)
        self.colour_drag = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.colour_highlight = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.intensity_label = self.add_label("", align=wx.ALIGN_CENTER_HORIZONTAL,
                                              colour=self.colour_highlight)

        self.phi = None             # Phi angle in radians
        self.phi_line_rad = None    # Phi drawing angle in radians (is phi -90)
        self.phi_line_pos = None    # End point in pixels of the Phi line
        self.phi_label = self.add_label("", colour=self.colour,
                                        align=wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_BOTTOM)
        self.theta = None           # Theta angle in radians
        self.theta_radius = None    # Radius of the theta circle in pixels
        self.theta_label = self.add_label("", colour=self.colour,
                                          align=wx.ALIGN_CENTER_HORIZONTAL)
        self.intersection = None    # The intersection of the circle and line in pixels

        self.dragging = False

        # Calculate the characteristic values for the first time
        self.on_size()

    # Property Getters/Setters
    @property
    def phi_rad(self):
        return self.phi

    @phi_rad.setter
    def phi_rad(self, phi_rad):
        self.phi = phi_rad
        self._calculate_phi()
        self.cnvs.Refresh()

    @property
    def phi_deg(self):
        return math.degrees(self.phi)

    @phi_deg.setter
    def phi_deg(self, phi_deg):
        self.phi_rad = math.radians(phi_deg)

    @property
    def theta_rad(self):
        return self.theta

    @theta_rad.setter
    def theta_rad(self, theta_rad):
        self.theta = theta_rad
        self.theta_radius = (theta_rad / (math.pi / 2)) * self.inner_radius
        self._calculate_theta()
        self.cnvs.Refresh()

    @property
    def theta_deg(self):
        return math.degrees(self.theta)

    @theta_deg.setter
    def theta_deg(self, theta_deg):
        self.theta_rad = math.radians(theta_deg)

    # END Property Getters/Setters

    def _calculate_phi(self, view_pos=None):
        """ Calcualate the Phi angle and the values to display the Phi line """
        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy

            # Calculate the phi angle in radians
            # Atan2 gives the angle between the positive x axis and the point
            # dx,dy
            self.phi = math.atan2(dx, dy) % self.tau

        if self.phi:
            self.phi_line_rad = self.phi - math.pi / 2

            cos_phi_line = math.cos(self.phi_line_rad)
            sin_phi_line = math.sin(self.phi_line_rad)

            # Pixel to which to draw the Phi line to
            phi_x = self.center_x + self.radius * cos_phi_line
            phi_y = self.center_y + self.radius * sin_phi_line
            self.phi_line_pos = (phi_x, phi_y)

            # Calc Phi label pos

            # Calculate the view point on the line where to place the label
            if self.theta_radius and self.theta_radius > self.inner_radius / 2:
                radius = self.inner_radius * 0.25
            else:
                radius = self.inner_radius * 0.75

            x = self.center_x + radius * cos_phi_line
            y = self.center_y + radius * sin_phi_line

            self.phi_label.text = u"φ %0.1f°" % math.degrees(self.phi)
            self.phi_label.deg = math.degrees(self.phi_line_rad)

            # Now we calculate a perpendicular offset to the Phi line where
            # we can plot the label. It is also determined if the label should
            # flip, depending on the angle.

            if self.phi < math.pi:
                ang = -math.pi / 2.0  # -45 deg
                self.phi_label.flip = False
            else:
                ang = math.pi / 2.0  # 45 deg
                self.phi_label.flip = True

            # Calculate a point further down the line that we will rotate
            # around the calculated label x,y. By translating (-x and -y) we
            # 'move' the origin to label x,y
            rx = (self.center_x - x) + (radius + 5) * cos_phi_line
            ry = (self.center_y - y) + (radius + 5) * sin_phi_line

            # Apply the rotation
            lx = rx * math.cos(ang) - ry * math.sin(ang)
            ly = rx * math.sin(ang) + ry * math.cos(ang)

            # Translate back to our original origin
            lx += x
            ly += y

            self.phi_label.pos = (lx, ly)

    def _calculate_theta(self, view_pos=None):
        """ Calculate the Theta angle and the values needed to display it. """
        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy
            # Get the radius and the angle for Theta
            self.theta_radius = min(math.sqrt(dx * dx + dy * dy),
                                    self.inner_radius)
            self.theta = (math.pi / 2) * (self.theta_radius / self.inner_radius)
        elif self.theta:
            self.theta_radius = (self.theta / (math.pi / 2)) * self.inner_radius
        else:
            return

        # Calc Theta label pos
        x = self.center_x
        y = self.center_y + self.theta_radius + 3

        theta_str = u"θ %0.1f°" % math.degrees(self.theta)

        self.theta_label.text = theta_str
        self.theta_label.pos = (x, y)

    def _calculate_intersection(self):
        if None not in (self.phi_line_rad, self.theta_radius):
            # Calculate the intersection between Phi and Theta
            x = self.center_x + self.theta_radius * math.cos(self.phi_line_rad)
            y = self.center_y + self.theta_radius * math.sin(self.phi_line_rad)
            self.intersection = (x, y)
        else:
            self.intersection = None

    def _calculate_display(self, view_pos=None):
        """ Calculate the values needed for plotting the Phi and Theta lines and labels

        If view_pos is not given, the current Phi and Theta angles will be used.

        """

        self._calculate_phi(view_pos)
        self._calculate_theta(view_pos)
        self._calculate_intersection()

        # if (view_pos and 0 < self.intersection[0] < self.cnvs.ClientSize.x and
        #         0 < self.intersection[1] < self.cnvs.ClientSize.y):
        #     # FIXME: Determine actual value here
        #     #self.intensity_label.text = ""
        #     pass

    # Event Handlers

    def on_left_down(self, evt):
        if self.active.value:
            self.dragging = True

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            self._calculate_display(evt.Position)
            self.dragging = False
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        # Only change the values when the user is dragging
        if self.active.value and self.dragging:
            self._calculate_display(evt.Position)
            self.cnvs.Refresh()
        else:
            base.ViewOverlay.on_motion(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            base.ViewOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_size(self, evt=None):
        # Calculate the characteristic values
        self.center_x = self.cnvs.ClientSize.x / 2
        self.center_y = self.cnvs.ClientSize.y / 2
        self.inner_radius = min(self.center_x, self.center_y)
        self.radius = self.inner_radius + (self.ticksize / 1.5)
        self.ticks = []

        # Top middle
        for i in range(self.num_ticks):
            # phi needs to be rotated 90 degrees counter clockwise, otherwise
            # 0 degrees will be at the right side of the circle
            phi = (self.tau / self.num_ticks * i) - (math.pi / 2)
            deg = round(math.degrees(phi))

            cos = math.cos(phi)
            sin = math.sin(phi)

            # Tick start and end poiint (outer and inner)
            ox = self.center_x + self.radius * cos
            oy = self.center_y + self.radius * sin
            ix = self.center_x + (self.radius - self.ticksize) * cos
            iy = self.center_y + (self.radius - self.ticksize) * sin

            # Tick label positions
            lx = self.center_x + (self.radius + 5) * cos
            ly = self.center_y + (self.radius + 5) * sin

            label = self.add_label(u"%d°" % (deg + 90),
                                   (lx, ly),
                                   colour=(0.8, 0.8, 0.8),
                                   deg=deg - 90,
                                   flip=True,
                                   align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM)

            self.ticks.append((ox, oy, ix, iy, label))

        self._calculate_display()

        if evt:
            base.ViewOverlay.on_size(self, evt)

    # END Event Handlers

    def draw(self, ctx):
        # Draw angle lines
        ctx.set_line_width(2.5)
        ctx.set_source_rgba(0, 0, 0, 0.2 if self.dragging else 0.5)

        if self.theta is not None:
            # Draw dark underline azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

        if self.phi is not None:
            # Draw dark underline Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

        # Light selection lines formatting
        ctx.set_line_width(2)
        ctx.set_dash([3])

        if self.dragging:
            ctx.set_source_rgba(*self.colour_drag)
        else:
            ctx.set_source_rgb(*self.colour)

        if self.theta is not None:
            # Draw azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

            self.theta_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        if self.phi is not None:
            # Draw Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

            self.phi_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        ctx.set_dash([])

        # ## Draw angle markings ###

        # Draw frame that covers everything outside the center circle
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgb(0.2, 0.2, 0.2)

        ctx.rectangle(0, 0, self.cnvs.ClientSize.x, self.cnvs.ClientSize.y)
        ctx.arc(self.center_x, self.center_y, self.inner_radius, 0, self.tau)
        # mouse_inside = not ctx.in_fill(float(self.vx or 0), float(self.vy or 0))
        ctx.fill()

        # Draw Azimuth degree circle
        ctx.set_line_width(2)
        ctx.set_source_rgb(0.5, 0.5, 0.5)
        ctx.arc(self.center_x, self.center_y, self.radius, 0, self.tau)
        ctx.stroke()

        # Draw Azimuth degree ticks
        ctx.set_line_width(1)
        for sx, sy, lx, ly, _ in self.ticks:
            ctx.move_to(sx, sy)
            ctx.line_to(lx, ly)
        ctx.stroke()

        # Draw tick labels, ignore padding in this case
        pad, self.canvas_padding = self.canvas_padding, 0

        for _, _, _, _, label in self.ticks:
            label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        self.canvas_padding = pad

        if self.intensity_label.text and self.intersection:
            ctx.set_source_rgb(*self.colour_highlight)
            ctx.arc(self.intersection[0], self.intersection[1], 3, 0, self.tau)
            ctx.fill()

            x, y = self.intersection
            y -= 18
            if y < 40:
                y += 40

            self.intensity_label.pos = (x, y)
            self.intensity_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)
