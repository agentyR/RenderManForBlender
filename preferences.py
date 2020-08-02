# ##### BEGIN MIT LICENSE BLOCK #####
#
# Copyright (c) 2015 - 2017 Pixar
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
#
# ##### END MIT LICENSE BLOCK #####

import bpy
import sys
import os
from bpy.types import AddonPreferences
from bpy.props import CollectionProperty, BoolProperty, StringProperty, FloatProperty
from bpy.props import IntProperty, PointerProperty, EnumProperty, FloatVectorProperty

from .rman_utils import filepath_utils
from . import rfb_logger
from . import rfb_icons

from .presets.properties import RendermanPresetCategory

class RendermanPreferencePath(bpy.types.PropertyGroup):
    name: StringProperty(name="", subtype='DIR_PATH')


class RendermanEnvVarSettings(bpy.types.PropertyGroup):
    if sys.platform == ("win32"):
        outpath = os.path.join(
            "C:", "Users", os.getlogin(), "Documents", "PRMan")
        out: StringProperty(
            name="OUT (Output Root)",
            description="Default RIB export path root",
            subtype='DIR_PATH',
            default='C:/tmp/renderman_for_blender/{blend}')

    else:
        outpath = os.path.join(os.environ.get('HOME'), "Documents", "PRMan")
        out: StringProperty(
            name="OUT (Output Root)",
            description="Default RIB export path root",
            subtype='DIR_PATH',
            default='/tmp/renderman_for_blender/{blend}')

class RendermanPreferences(AddonPreferences):
    bl_idname = __package__

    # find the renderman options installed
    def find_installed_rendermans(self, context):
        options = [('NEWEST', 'Newest Version Installed',
                    'Automatically updates when new version installed. NB: If an RMANTREE environment variable is set, this will always take precedence.')]
        for vers, path in filepath_utils.get_installed_rendermans():
            options.append((path, vers, path))
        return options

    rmantree_choice: EnumProperty(
        name='RenderMan Version to use',
        description='Leaving as "Newest" will automatically update when you install a new RenderMan version',
        # default='NEWEST',
        items=find_installed_rendermans
    )

    rmantree_method: EnumProperty(
        name='RenderMan Location',
        description='''How RenderMan should be detected.  Most users should leave to "Detect". 
                    Users should restart Blender after making a change.
                    ''',
        items=[('ENV', 'Get From RMANTREE Environment Variable',
                'This will use the RMANTREE set in the enviornment variables'),
                ('DETECT', 'Choose From Installed', 
                '''This will scan for installed RenderMan locations to choose from.'''),
                ('MANUAL', 'Set Manually', 'Manually set the RenderMan installation (for expert users)')],
        default='ENV')

    path_rmantree: StringProperty(
        name="RMANTREE Path",
        description="Path to RenderMan Pro Server installation folder",
        subtype='DIR_PATH',
        default='')

    draw_ipr_text: BoolProperty(
        name="Draw IPR Text",
        description="Draw notice on View3D when IPR is active",
        default=True)

    draw_panel_icon: BoolProperty(
        name="Draw Panel Icon",
        description="Draw an icon on RenderMan Panels",
        default=True)

    path_fallback_textures_path: StringProperty(
        name="Fallback Texture Path",
        description="Fallback path for textures, when the current directory is not writable",
        subtype='FILE_PATH',
        default=os.path.join('{OUT}', 'textures'))        

    rman_scene_version_padding: IntProperty(
        name="Version Padding",
        description="The number of zeros to pad the version token",
        default=3,
        min=1, max=4
    )
    rman_scene_take_padding: IntProperty(
        name="Take Padding",
        description="The number of zeros to pad the take token",
        default=2,
        min=1, max=4
    )    

    rman_scene_version_increment: EnumProperty(
        name="Increment Version",
        description="The version number can be set to automatically increment each time you render",
        items=[
            ('MANUALLY', 'Manually', ''),
            ('RENDER', 'On Render', ''),
            ('BATCH RENDER', 'On Batch Render', '')
        ],
        default='MANUALLY'
    )

    rman_scene_take_increment: EnumProperty(
        name="Increment Take",
        description="The take number can be set to automatically increment each time you render",
        items=[
            ('MANUALLY', 'Manually', ''),
            ('RENDER', 'On Render', ''),
            ('BATCH RENDER', 'On Batch Render', '')
        ],        
        default='MANUALLY'
    )    

    env_vars: PointerProperty(
        type=RendermanEnvVarSettings,
        name="Environment Variable Settings")

    auto_check_update: bpy.props.BoolProperty(
        name = "Auto-check for Update",
        description = "If enabled, auto-check for updates using an interval",
        default = True,
        )

    def update_rman_logging_level(self, context):
        level = rfb_logger.__LOG_LEVELS__[self.rman_logging_level]
        rfb_logger.set_logger_level(level)

    rman_logging_level: EnumProperty(
        name='Logging Level',
        description='''Log level verbosity. Advanced: Setting the RFB_LOG_LEVEL environment variable will override this preference. Requires a restart.
                    ''',
        items=[('CRITICAL', 'Critical', ''),
                ('ERROR', 'Error', ''),
                ('WARNING', 'Warning', ''),
                ('INFO', 'Info', ''),
                ('VERBOSE', 'Verbose', ''),
                ('DEBUG', 'Debug', ''),
        ],
        default='WARNING',
        update=update_rman_logging_level)

    rman_logging_file: StringProperty(
        name='Logging File',
        description='''A file to write logging to. This will always write at DEBUG level. Setting the RFB_LOG_FILE environment variable will override this preference. Requires a restart.''',
        default = '',
        subtype='FILE_PATH'
    )

    rman_do_preview_renders: BoolProperty(
        name="Render Previews",
        description="Enable rendering of material previews. This is considered a WIP.",
        default=False)

    rman_preview_renders_minSamples: IntProperty(
        name="Preview Min Samples",
        description="Minimum samples for preview renders",
        default=0,
        min=0, soft_max=4,
    )
    rman_preview_renders_maxSamples: IntProperty(
        name="Preview Max Samples",
        description="Maximum samples for preview renders",
        default=1,
        min=1, soft_max=4,
    )  
    rman_preview_renders_pixelVariance: FloatProperty(
        name="Pixel Variance",
        description="Maximum samples for preview renders",
        default=0.15,
        min=0.001, soft_max=0.5,
    )          

    rman_viewport_crop_color: FloatVectorProperty(
        name="CropWindow Color",
        description="Color of the cropwindow border in the viewport when in IPR.",
        default=(0.0, 0.498, 1.0, 1.0), 
        size=4,
        subtype="COLOR")     

    rman_viewport_bucket_color: FloatVectorProperty(
        name="Bucket Marker Color",
        description="Color of the bucket markers in the viewport when in IPR.",
        default=(0.0, 0.498, 1.0, 1.0), 
        size=4,
        subtype="COLOR")     

    rman_editor: StringProperty(
        name="Editor",
        subtype='FILE_PATH',
        description="Text editor excutable you want to use to view RIB.",
        default=""
    )

    rman_do_cycles_convert: BoolProperty(
        name="Convert Cycles Nodes",
        default=False,
        description="Add convert Cycles Networks buttons to the material properties panel. N.B.: This isn't guaranteed to fully convert Cycles networks successfully. Also, because of differences in OSL implementations, converted networks may cause stability problems when rendering."

    )

    rman_render_nurbs_as_mesh: BoolProperty(
        name="NURBS as Mesh",
        default=False,
        description="Render all NURBS surfaces as meshes."
    )

    rman_emit_default_params: BoolProperty(
        name="Emit Default Params",
        default=False,
        description="Controls whether or not parameters that are not changed from their defaults should be emitted to RenderMan. Turning this on is only useful for debugging purposes."
    )

    rman_show_advanced_params: BoolProperty(
        name="Show Advanced",
        default=False,
        description="Show advanced preferences"
    )

    rman_config_dir: StringProperty(
        name="Config Directory",
        subtype='DIR_PATH',
        description="Path to JSON configuration files. Requires a restart.",
        default=""
    )    

    rman_viewport_refresh_rate: FloatProperty(
        name="Viewport Refresh Rate",
        description="The number of seconds to wait before the viewport refreshes during IPR.",
        default=0.01,
        precision=5,
        min=0.00001,
        max=0.1
    )    

    presets_current_category: PointerProperty(
        type=RendermanPresetCategory,
    )
    presets_root_category: PointerProperty(
        type=RendermanPresetCategory,
    )     
    presets_current_category_path: StringProperty(default='')
    presets_show_large_icons: BoolProperty(
        name="Show Large Icons",
        description="Turns this off if you do not want to see the large version of the preset's icon",
        default=True
    )    
    presets_show_subcategories: BoolProperty(
        name="Show Subcategories",
        description="By default, we only show presets in the current category. Turn this on if you want to also show subcategories (can be slow if there are large number of presets).",
        default=False
    )

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False        
        layout = self.layout

        rman_r_icon = rfb_icons.get_icon("rman_blender")

        row = layout.row()
        row.use_property_split = False
        col = row.column()
        col.prop(self, 'rmantree_method')
        if self.rmantree_method == 'DETECT':
            col.prop(self, 'rmantree_choice')
            if self.rmantree_choice == 'NEWEST':
                col.label(text="RMANTREE: %s " % filepath_utils.guess_rmantree())
        elif self.rmantree_method == 'ENV':
            col.label(text="RMANTREE: %s" % filepath_utils.rmantree_from_env())
        else:
            col.prop(self, "path_rmantree")
        if filepath_utils.guess_rmantree() is None:
            row = layout.row()
            row.alert = True
            row.label(text='Error in RMANTREE. Reload addon to reset.', icon='ERROR')

        # Behavior Prefs
        row = layout.row()
        row.label(text='Behavior', icon_value=rman_r_icon.icon_id)
        row = layout.row()
        col = row.column()
        col.prop(self, 'rman_do_preview_renders')  
        col.prop(self, 'rman_render_nurbs_as_mesh')
        col.prop(self, 'rman_do_cycles_convert')     
        col.prop(self, 'rman_emit_default_params')    

        # Workspace
        env = self.env_vars
        row = layout.row()
        row.label(text='Workspace', icon_value=rman_r_icon.icon_id)
        row = layout.row()
        col = row.column()
        col.prop(env, "out")
        col.prop(self, 'path_fallback_textures_path')
        col.prop(self, "rman_scene_version_padding")
        col.prop(self, "rman_scene_take_padding")
        col.prop(self, "rman_scene_version_increment")
        col.prop(self, "rman_scene_take_increment")

        # UI Prefs
        row = layout.row()
        row.label(text='UI', icon_value=rman_r_icon.icon_id)
        row = layout.row()
        col = row.column()
        col.prop(self, 'rman_viewport_crop_color')
        col.prop(self, 'rman_viewport_bucket_color')        
        col.prop(self, 'draw_ipr_text')
        col.prop(self, 'draw_panel_icon')
        col.prop(self, 'rman_editor')

        # Preset Browser
        row = layout.row()
        row.label(text='Preset Browser', icon_value=rman_r_icon.icon_id)
        row = layout.row()
        col = row.column()
        if self.presets_root_category:
            col.label(text='Path: %s' % self.presets_root_category.path)
        col.prop(self, 'presets_show_large_icons')
        col.prop(self, 'presets_show_subcategories')

        # Logging
        row = layout.row()
        row.label(text='Logging', icon_value=rman_r_icon.icon_id)
        row = layout.row()
        col = row.column()
        col.prop(self, 'rman_logging_level')
        col.prop(self, 'rman_logging_file')

        row = layout.row()   
        row.label(text='Advanced', icon_value=rman_r_icon.icon_id)
        row = layout.row()

        ui_open = getattr(self, 'rman_show_advanced_params')
        icon = 'DISCLOSURE_TRI_DOWN' if ui_open \
            else 'DISCLOSURE_TRI_RIGHT'

        row.prop(self, 'rman_show_advanced_params', icon=icon, text='',
            icon_only=True, emboss=False)              

        row = layout.row()
        col = row.column() 

        if ui_open:
            col.prop(self, 'rman_viewport_refresh_rate')  
            col.prop(self, 'rman_config_dir')   
            if self.rman_do_preview_renders:
                col.prop(self, 'rman_preview_renders_minSamples')
                col.prop(self, 'rman_preview_renders_maxSamples')
                col.prop(self, 'rman_preview_renders_pixelVariance') 

def register():
    bpy.utils.register_class(RendermanPreferencePath)
    bpy.utils.register_class(RendermanEnvVarSettings)
    bpy.utils.register_class(RendermanPreferences)


def unregister():
    bpy.utils.unregister_class(RendermanPreferences)
    bpy.utils.unregister_class(RendermanEnvVarSettings)
    bpy.utils.unregister_class(RendermanPreferencePath)
