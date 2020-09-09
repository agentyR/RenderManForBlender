from ..rman_utils.node_desc import NodeDesc
from ..rman_utils import filepath_utils
from ..rman_utils.filepath import FilePath
from ..rman_utils import texture_utils
from ..rman_utils import property_utils
from ..rfb_logger import rfb_log
from .rman_socket_utils import node_add_inputs
from .rman_socket_utils import node_add_outputs
from .rman_socket_utils import update_inputs
from .. import rman_config
from .. import rman_render
from ..rman_properties import rman_properties_scene
from ..rman_properties import rman_properties_renderlayers
from ..rman_properties import rman_properties_world
from ..rman_properties import rman_properties_camera
from ..rman_constants import RFB_ADDON_PATH
from ..rman_constants import RFB_ARRAYS_MAX_LEN
from nodeitems_utils import NodeCategory, NodeItem
from collections import OrderedDict
from operator import attrgetter
from bpy.props import *
from copy import deepcopy
import bpy
import os
import sys
import traceback
import nodeitems_utils

# registers
from . import rman_bl_nodes_sockets
from . import rman_bl_nodes_shaders
from . import rman_bl_nodes_ops
from . import rman_bl_nodes_props
from . import rman_bl_nodes_menus

__RMAN_DISPLAY_NODES__ = []
__RMAN_BXDF_NODES__ = []
__RMAN_DISPLACE_NODES__ = []
__RMAN_INTEGRATOR_NODES__ = []
__RMAN_PROJECTION_NODES__ = []
__RMAN_DISPLAYFILTER_NODES__ = []
__RMAN_SAMPLEFILTER_NODES__ = []
__RMAN_PATTERN_NODES__ = []
__RMAN_LIGHT_NODES__ = []
__RMAN_LIGHTFILTER_NODES__ = []
__RMAN_NODE_TYPES__ = dict()

__RMAN_NODE_CATEGORIES__ = dict()
__RMAN_NODE_CATEGORIES__['bxdf'] = dict()
__RMAN_NODE_CATEGORIES__['light'] = dict()
__RMAN_NODE_CATEGORIES__['pattern'] = dict()
__RMAN_NODE_CATEGORIES__['displace'] = dict()
__RMAN_NODE_CATEGORIES__['samplefilter'] = dict()
__RMAN_NODE_CATEGORIES__['displayfilter'] = dict()
__RMAN_NODE_CATEGORIES__['integrator'] = dict()


__RMAN_NODE_CATEGORIES__['bxdf']['bxdf_misc'] = (('RenderMan Misc Bxdfs', []), [])
__RMAN_NODE_CATEGORIES__['light']['light'] = (('RenderMan Lights', []), [])
__RMAN_NODE_CATEGORIES__['pattern']['patterns_misc'] = (('RenderMan Misc Patterns', []), [])
__RMAN_NODE_CATEGORIES__['displace']['displace'] = (('RenderMan Displacements', []), [])
__RMAN_NODE_CATEGORIES__['samplefilter']['samplefilter'] = (('RenderMan SampleFilters', []), [])
__RMAN_NODE_CATEGORIES__['displayfilter']['displayfilter'] = (('RenderMan DisplayFilters', []), [])
__RMAN_NODE_CATEGORIES__['integrator']['integrator'] = (('RenderMan Integrators', []), [])
  

__RMAN_NODES__ = { 
    'displaydriver': __RMAN_DISPLAY_NODES__,
    'bxdf': __RMAN_BXDF_NODES__,
    'displace': __RMAN_DISPLACE_NODES__,
    'integrator': __RMAN_INTEGRATOR_NODES__,
    'projection': __RMAN_PROJECTION_NODES__,
    'displayfilter': __RMAN_DISPLAYFILTER_NODES__, 
    'samplefilter': __RMAN_SAMPLEFILTER_NODES__,
    'pattern': __RMAN_PATTERN_NODES__,
    'light': __RMAN_LIGHT_NODES__,
    'lightfilter': __RMAN_LIGHTFILTER_NODES__
}

__RMAN_PLUGIN_MAPPING__ = {
    'displaydriver': rman_properties_renderlayers.RendermanAOV,
    'projection': rman_properties_camera.RendermanCameraSettings
}

__RMAN_NODES_NO_REGISTER__ = [
        'PxrCombinerLightFilter.args', 
        'PxrSampleFilterCombiner.args', 
        'PxrDisplayFilterCombiner.args', 
        'PxrShadowDisplayFilter.args',
        'PxrShadowFilter.args',
        'PxrDisplace.oso'
]
__RMAN_NODES_ALREADY_REGISTERED__ = False

def update_conditional_visops(node):
    for param_name, prop_meta in getattr(node, 'prop_meta').items():
        if 'conditionalVisOp' in prop_meta:
            cond_expr = prop_meta['conditionalVisOp']['expr']
            try:
                hidden = not eval(cond_expr)
                prop_meta['hidden'] = hidden
                if hasattr(node, 'inputs') and param_name in node.inputs:
                    node.inputs[param_name].hide = hidden
            except:
                print("Error in conditional visop: %s" % (cond_expr))

def assetid_update_func(self, context):
    node = self.node if hasattr(self, 'node') else self
    light = None
    active = context.active_object
    if active.type == 'LIGHT':
        light = active.data
           
    texture_utils.update_texture(node, light=light)   
    if context and hasattr(context, 'material'):
        mat = context.material
        if mat:
            node.update_mat(mat)
    elif context and hasattr(context, 'node'):
        mat = context.space_data.id
        if mat:
            node.update_mat(mat)  

def update_func_with_inputs(self, context):
    # check if this prop is set on an input
    node = self.node if hasattr(self, 'node') else self

    if context and hasattr(context, 'active_object'):
        if context.active_object.type == 'LIGHT':
            context.active_object.update_tag(refresh={'DATA'})

    if context and hasattr(context, 'material'):
        mat = context.material
        if mat:
            node.update_mat(mat)
    elif context and hasattr(context, 'node'):
        mat = context.space_data.id
        if mat:
            node.update_mat(mat)

    # update the conditional_vis_ops
    update_conditional_visops(node)

    if node.bl_idname in ['PxrLayerPatternOSLNode', 'PxrSurfaceBxdfNode']:
        node_add_inputs(node, node.name, node.prop_names)
    else:
        update_inputs(node)

    # set any inputs that are visible and param is hidden to hidden
    prop_meta = getattr(node, 'prop_meta')
    if hasattr(node, 'inputs'):
        for input_name, socket in node.inputs.items():
            if 'hidden' in prop_meta[input_name]:
                socket.hide = prop_meta[input_name]['hidden']

def update_array_size_func(self, context):
    '''
    Callback function for changes to array size/length property

    If there's a change in the size, we first remove all of the input/sockets
    from the ShadingNode related to arrays. We then re-add the input/socktes
    with the new size via the rman_socket_utils.node_add_inputs function. 
    We need to do this because Blender seems to draw all inputs in the node
    properties panel. This is a problem if the array size gets smaller.
    '''

    # check if this prop is set on an input
    node = self.node if hasattr(self, 'node') else self

    if context and hasattr(context, 'active_object'):
        if context.active_object and context.active_object.type == 'LIGHT':
            context.active_object.update_tag(refresh={'DATA'})    

    if context and hasattr(context, 'material'):
        mat = context.material
        if mat:
            node.update_mat(mat)
    elif context and hasattr(context, 'node'):
        mat = context.space_data.id
        if mat:
            node.update_mat(mat)
    
    # first remove all sockets/inputs from the node related to arrays
    for prop_name,meta in node.prop_meta.items():
        renderman_type = meta.get('renderman_type', '')
        if renderman_type == 'array':
            sub_prop_names = getattr(node, prop_name)
            for nm in sub_prop_names:
                if nm in node.inputs.keys():
                    node.inputs.remove(node.inputs[nm])

    # now re-add all sockets/inputs
    node_add_inputs(node, node.name, node.prop_names)
           

def update_func(self, context):
    # check if this prop is set on an input
    node = self.node if hasattr(self, 'node') else self

    if context and hasattr(context, 'active_object'):
        if context.active_object and context.active_object.type == 'LIGHT':
            context.active_object.update_tag(refresh={'DATA'})

    if context and hasattr(context, 'material'):
        mat = context.material
        if mat:
            node.update_mat(mat)

    elif context and hasattr(context, 'node'):
        mat = context.space_data.id
        if mat:
            node.update_mat(mat)
    # update the conditional_vis_ops
    update_conditional_visops(node)

    # set any inputs that are visible and param is hidden to hidden
    prop_meta = getattr(node, 'prop_meta')
    if hasattr(node, 'inputs'):
        for input_name, socket in node.inputs.items():
            if input_name not in prop_meta:
                continue 
            if 'hidden' in prop_meta[input_name] \
                    and prop_meta[input_name]['hidden'] and not socket.hide:
                socket.hide = True      

def update_integrator_func(self, context):
    rr = rman_render.RmanRender.get_rman_render()
    if rr.rman_interactive_running:
        rr.rman_scene_sync.update_integrator(context)    

def class_generate_properties(node, parent_name, node_desc):
    prop_names = []
    prop_meta = {}
    output_meta = OrderedDict()

    if "__annotations__" not in node.__dict__:
            setattr(node, "__annotations__", {})

    # pxr osl and seexpr need these to find the code
    if parent_name in ["PxrOSL", "PxrSeExpr"]:
        # Enum for internal, external type selection
        EnumName = "codetypeswitch"
        if parent_name == 'PxrOSL':
            EnumProp = EnumProperty(items=(('EXT', "External", ""),
                                           ('INT', "Internal", "")),
                                    name="Shader Location", default='INT')
        else:
            EnumProp = EnumProperty(items=(('NODE', "Node", ""),
                                           ('INT', "Internal", "")),
                                    name="Expr Location", default='NODE')

        EnumMeta = {'renderman_name': 'filename',
                    'name': 'codetypeswitch',
                    'renderman_type': 'string',
                    'default': '', 'label': 'codetypeswitch',
                    'type': 'enum', 'options': '',
                    'widget': 'mapper', '__noconnection': True}
        node.__annotations__[EnumName] = EnumProp
        prop_names.append(EnumName)
        prop_meta[EnumName] = EnumMeta
        # Internal file search prop
        InternalName = "internalSearch"
        InternalProp = StringProperty(name="Shader to use",
                                      description="Storage space for internal text data block",
                                      default="")
        InternalMeta = {'renderman_name': 'filename',
                        'name': 'internalSearch',
                        'renderman_type': 'string',
                        'default': '', 'label': 'internalSearch',
                        'type': 'string', 'options': '',
                        'widget': 'fileinput', '__noconnection': True}
        node.__annotations__[InternalName] = InternalProp
        prop_names.append(InternalName)
        prop_meta[InternalName] = InternalMeta
        # External file prop
        codeName = "shadercode"
        codeProp = StringProperty(name='External File', default='',
                                  subtype="FILE_PATH", description='')
        codeMeta = {'renderman_name': 'filename',
                    'name': 'ShaderCode', 'renderman_type': 'string',
                    'default': '', 'label': 'ShaderCode',
                    'type': 'string', 'options': '',
                    'widget': 'fileinput', '__noconnection': True}
        node.__annotations__[codeName] = codeProp
        prop_names.append(codeName)
        prop_meta[codeName] = codeMeta

    # inputs

    for node_desc_param in node_desc.params:

        update_function = None
        if node_desc.node_type == 'integrator':
            update_function = update_integrator_func
        else:
            param_widget = node_desc_param.widget.lower() if hasattr(node_desc_param,'widget') else 'default'
            if param_widget == 'fileinput' or param_widget == 'assetidinput' or (param_widget == 'default' and node_desc_param.name == 'filename'):
                update_function = assetid_update_func
            else:
                update_function = update_func_with_inputs if 'enable' in node_desc_param.name else update_func         

        if not update_function:
            update_function = update_func

        if node_desc_param.is_array():
            # this is an array 
            if property_utils.generate_array_property(node, prop_names, prop_meta, node_desc_param, update_array_size_func=update_array_size_func, update_array_elem_func=update_function):
                continue

        name, meta, prop = property_utils.generate_property(node, node_desc_param, update_function=update_function)
        if name is None:
            continue          
        if hasattr(node_desc_param, 'page') and node_desc_param.page != '':
            page = node_desc_param.page
            tokens = page.split('|')
            sub_prop_names = prop_names
            page_name = tokens[0]
                 
            if page_name not in prop_meta:
                # For pages, add a BoolProperty called '[page_name].uio'
                # This determines whether the page is opened or closed
                sub_prop_names.append(page_name)
                prop_meta[page_name] = {'renderman_type': 'page'}
                ui_label = "%s_uio" % page_name
                dflt = getattr(node_desc_param, 'page_open', False)                
                node.__annotations__[ui_label] = BoolProperty(name=ui_label, default=dflt)
                setattr(node, page_name, [])   

                # If this a PxrSurface node, add an extra BoolProperty to control
                # enabling/disabling each lobe
                if parent_name == 'PxrSurface' and 'Globals' not in page_name:
                    enable_param_name = 'enable' + page_name.replace(' ', '')
                    if enable_param_name not in prop_meta:
                        prop_meta[enable_param_name] = {
                            'renderman_type': 'enum', 'renderman_name': enable_param_name}
                        default = page_name == 'Diffuse'
                        enable_param_prop = BoolProperty(name="Enable " + page_name,
                                            default=bool(default),
                                            update=update_func_with_inputs)
                        node.__annotations__[enable_param_name] = enable_param_prop        
                        page_prop_names = getattr(node, page_name)   
                        if enable_param_name not in page_prop_names:     
                            page_prop_names.append(enable_param_name)
                            setattr(node, page_name, page_prop_names) 

            if len(tokens) > 1:

                for i in range(1, len(tokens)):
                    parent_page = page_name
                    page_name += '.' + tokens[i]
                    if page_name not in prop_meta:
                        prop_meta[page_name] = {'renderman_type': 'page'}
                        ui_label = "%s_uio" % page_name
                        dflt = getattr(node_desc_param, 'page_open', False) 
                        node.__annotations__[ui_label] = BoolProperty(name=ui_label, default=dflt)
                        setattr(node, page_name, [])
                    
                    sub_prop_names = getattr(node, parent_page)
                    if page_name not in sub_prop_names:
                        sub_prop_names.append(page_name)
                        setattr(node, parent_page, sub_prop_names)

            sub_prop_names = getattr(node, page_name)
            sub_prop_names.append(name)
            setattr(node, page_name, sub_prop_names)           
            prop_meta[name] = meta
            node.__annotations__[name] = prop  

        else:
            prop_names.append(name)
            prop_meta[name] = meta
            node.__annotations__[name] = prop

    # outputs
    for node_desc_param in node_desc.outputs:
        renderman_type = node_desc_param.type
        prop_name = node_desc_param.name

        output_prop_meta = dict()
        if hasattr(node_desc_param, 'vstructmember'):
            output_prop_meta['vstructmember'] = node_desc_param.vstructmember
        if hasattr(node_desc_param, 'vstructConditionalExpr'):
            output_prop_meta['vstructConditionalExpr'] = node_desc_param.vstructConditionalExpr
        if hasattr(node_desc_param, 'vstruct'):
            output_prop_meta['vstruct'] = True
        output_prop_meta['name'] = node_desc_param.name
        output_meta[prop_name] = output_prop_meta
        output_meta[prop_name]['renderman_type'] = renderman_type       
            
    setattr(node, 'prop_names', prop_names)
    setattr(node, 'prop_meta', prop_meta)
    setattr(node, 'output_meta', output_meta)

def generate_node_type(node_desc, is_oso=False):
    ''' Dynamically generate a node type from pattern '''

    name = node_desc.name
    nodeType = node_desc.node_type
    typename = '%s%sNode' % (name, nodeType.capitalize())
    if is_oso:
        typename = '%s%sOSLNode' % (name, nodeType.capitalize())

    nodeDict = {'bxdf': rman_bl_nodes_shaders.RendermanBxdfNode,
                'pattern': rman_bl_nodes_shaders.RendermanPatternNode,
                'displace': rman_bl_nodes_shaders.RendermanDisplacementNode,
                'light': rman_bl_nodes_shaders.RendermanLightNode,
                'lightfilter': rman_bl_nodes_shaders.RendermanLightfilterNode,
                'samplefilter': rman_bl_nodes_shaders.RendermanSamplefilterNode,
                'displayfilter': rman_bl_nodes_shaders.RendermanDisplayfilterNode,
                'integrator': rman_bl_nodes_shaders.RendermanIntegratorNode}

    if nodeType not in nodeDict.keys():
        return (None, None)
    ntype = type(typename, (nodeDict[nodeType],), {})
    '''
    if is_oso:
        ntype.bl_label = '%s.oso' % name
    else:
        ntype.bl_label = name
    '''
    ntype.bl_label = name
    ntype.typename = typename

    def init(self, context):
        # add input/output sockets to nodes, based on type
        if self.renderman_node_type == 'bxdf':
            self.outputs.new('RendermanNodeSocketBxdf', "Bxdf") #.type = 'SHADER'
            #socket_template = self.socket_templates.new(identifier='Bxdf', name='Bxdf', type='SHADER')
            node_add_inputs(self, name, self.prop_names)
            node_add_outputs(self)
            # if this is PxrLayerSurface set the diffusegain to 0.  The default
            # of 1 is unintuitive
            if self.plugin_name == 'PxrLayerSurface':
                self.diffuseGain = 0
        elif self.renderman_node_type == 'light':
            node_add_inputs(self, name, self.prop_names)
            self.outputs.new('RendermanNodeSocketLight', "Light")
        elif self.renderman_node_type == 'lightfilter':
            node_add_inputs(self, name, self.prop_names)
            self.outputs.new('RendermanNodeSocketLightFilter', "LightFilter")            
        elif self.renderman_node_type == 'displace':
            self.outputs.new('RendermanNodeSocketDisplacement', "Displacement")
            node_add_inputs(self, name, self.prop_names)
        elif self.renderman_node_type == 'displayfilter':
            self.outputs.new('RendermanNodeSocketDisplayFilter', "DisplayFilter")
            node_add_inputs(self, name, self.prop_names)            
        elif self.renderman_node_type == 'samplefilter':
            self.outputs.new('RendermanNodeSocketSampleFilter', "SampleFilter")
            node_add_inputs(self, name, self.prop_names)    
        elif self.renderman_node_type == 'integrator':
            self.outputs.new('RendermanNodeSocketIntegrator', "Integrator")
            node_add_inputs(self, name, self.prop_names)                           
        # else pattern
        elif name == "PxrOSL":
            self.outputs.clear()
        else:
            node_add_inputs(self, name, self.prop_names)
            node_add_outputs(self)
        
        # deal with any ramps necessary
        color_rman_ramps = self.__annotations__.get('__COLOR_RAMPS__', [])
        float_rman_ramps = self.__annotations__.get('__FLOAT_RAMPS__', [])

        if color_rman_ramps or float_rman_ramps:
            node_group = bpy.data.node_groups.new(
                '__RMAN_FAKE_NODEGROUP__', 'ShaderNodeTree') 
            node_group.use_fake_user = True                 
            self.rman_fake_node_group = node_group.name    

            for ramp_name in color_rman_ramps:
                n = node_group.nodes.new('ShaderNodeValToRGB')

                knots = None
                knots_name = '%s_Knots' % ramp_name
                cols = None
                cols_name = '%s_Colors' % ramp_name
                for node_desc_param in node_desc.params:
                    if node_desc_param.name == knots_name:
                        knots = node_desc_param
                    elif node_desc_param.name == cols_name:
                        cols = node_desc_param
                elements = n.color_ramp.elements
                prev_val = None
                for i in range(0, len(knots.default)):
                    if not prev_val:
                        prev_val = cols.default[i]
                    elif prev_val == cols.default[i]:
                        continue
                    prev_val = cols.default[i]
                    new_elem = elements.new(knots.default[i])
                    new_elem.color = (cols.default[i][0], cols.default[i][1], cols.default[i][2], 1.0)
                
                setattr(self, ramp_name, n.name)

            for ramp_name in float_rman_ramps:
                n = node_group.nodes.new('ShaderNodeVectorCurve') 

                knots = None
                knots_name = '%s_Knots' % ramp_name
                vals = None
                vals_name = '%s_Floats' % ramp_name
                for node_desc_param in node_desc.params:
                    if node_desc_param.name == knots_name:
                        knots = node_desc_param
                    elif node_desc_param.name == vals_name:
                        vals = node_desc_param                
                curve = n.mapping.curves[0]
                points = curve.points
                prev_val = None
                for i in range(0, len(knots.default)):
                    if not prev_val:
                        prev_val = vals.default[i]
                    elif prev_val == vals.default[i]:
                        continue      
                    prev_val = vals.default[i]              
                    points.new(knots.default[i], vals.default[i])

                setattr(self, ramp_name, n.name)        

            self.__annotations__['__COLOR_RAMPS__'] = color_rman_ramps
            self.__annotations__['__FLOAT_RAMPS__'] =  float_rman_ramps

        update_conditional_visops(self)


    def free(self):
        if self.rman_fake_node_group in bpy.data.node_groups:
            bpy.data.node_groups.remove(bpy.data.node_groups[self.rman_fake_node_group])

    ntype.init = init
    ntype.free = free
    
    if "__annotations__" not in ntype.__dict__:
            setattr(ntype, "__annotations__", {})

    # the name of our fake node group to hold all of our ramp nodes
    ntype.__annotations__['rman_fake_node_group'] = StringProperty('__rman_ramps__', default='')

    ntype.__annotations__['plugin_name'] = StringProperty(name='Plugin Name',
                                       default=name, options={'HIDDEN'})

    class_generate_properties(ntype, name, node_desc)
    if nodeType == 'light':
        ntype.__annotations__['light_shading_rate'] = FloatProperty(
            name="Light Shading Rate",
            description="Shading Rate for this light.  \
                Leave this high unless detail is missing",
            default=100.0)
        ntype.__annotations__['light_primary_visibility'] = BoolProperty(
            name="Light Primary Visibility",
            description="Camera visibility for this light",
            default=True)

    bpy.utils.register_class(ntype)

    return (typename, ntype)

def register_plugin_to_parent(ntype, name, node_desc, plugin_type, parent):

    class_generate_properties(ntype, name, node_desc)
    setattr(ntype, 'renderman_node_type', plugin_type)
    
    if "__annotations__" not in parent.__dict__:
            setattr(parent, "__annotations__", {})

    # register and add to scene_settings
    bpy.utils.register_class(ntype)
    settings_name = "%s_settings" % name
    parent.__annotations__["%s_settings" % name] = PointerProperty(type=ntype, name="%s Settings" % name)
    
    if "__annotations__" not in rman_properties_world.RendermanWorldSettings.__dict__:
            setattr(rman_properties_world.RendermanWorldSettings, "__annotations__", {})

def register_plugin_types(node_desc):

    items = []

    if node_desc.node_type not in __RMAN_PLUGIN_MAPPING__:
        return
    parent = __RMAN_PLUGIN_MAPPING__[node_desc.node_type]
    name = node_desc.name
    if node_desc.node_type == 'displaydriver':
        # remove the d_ prefix
        name = name.split('d_')[1]
    typename = name + node_desc.node_type.capitalize() + 'Settings'
    ntype = type(typename, (rman_bl_nodes_props.RendermanPluginSettings,), {})
    ntype.bl_label = name
    ntype.typename = typename
    ntype.bl_idname = typename
    ntype.plugin_name = name

    try:
        register_plugin_to_parent(ntype, name, node_desc, node_desc.node_type, parent)
    except Exception as e:
        rfb_log().error("Error registering plugin ", name)
        traceback.print_exc()

def get_path_list():
    paths = []
    rmantree = filepath_utils.guess_rmantree()
    paths.append(os.path.join(rmantree, 'lib', 'shaders'))    
    paths.append(os.path.join(rmantree, 'lib', 'plugins', 'Args'))
    paths.append(os.path.join(RFB_ADDON_PATH, 'Args'))

    if 'RMAN_SHADERPATH' in os.environ:
        RMAN_SHADERPATH = os.environ['RMAN_SHADERPATH']
        for p in RMAN_SHADERPATH.split(os.path.pathsep):
            paths.append(p)

    if 'RMAN_RIXPLUGINPATH' in os.environ:
        RMAN_RIXPLUGINPATH = os.environ['RMAN_RIXPLUGINPATH']
        for p in RMAN_RIXPLUGINPATH.split(os.path.pathsep):
            paths.append(os.path.join(p, 'Args'))

    return paths
                     
class RendermanShaderNodeCategory(NodeCategory):

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'

def register_rman_nodes():
    global __RMAN_NODE_CATEGORIES__

    rfb_log().debug("Registering RenderMan Plugin Nodes:")
    path_list = get_path_list()
    visited = set()
    for path in path_list:
        for root, dirnames, filenames in os.walk(path):
            # Prune this branch if we've already visited it (e.g., one path
            # in the path list is actually a subdirectory of another).
            real = os.path.realpath(root)
            if real in visited:
                dirnames[:] = []
                continue
            visited.add(real)

            for filename in filenames:        
                if filename.endswith(('.args', '.oso')):
                    # skip registering these nodes
                    if filename in __RMAN_NODES_NO_REGISTER__:
                        continue       
                    is_oso = False 
                    is_args = True 
                    if filename.endswith('.oso'):
                        is_oso = True
                        is_args = False

                    node_desc = NodeDesc(FilePath(root).join(FilePath(filename)))

                    # apply any overrides
                    rman_config.apply_args_overrides(filename, node_desc)

                    __RMAN_NODES__[node_desc.node_type].append(node_desc)
                    rfb_log().debug("\t%s" % node_desc.name)

                    # These plugin types are special. They are not actually shading
                    # nodes that can be used in Blender's shading editor, but 
                    # we still create PropertyGroups for them so they can be inserted
                    # into the correct UI panel.
                    if node_desc.node_type in ['projection', 'displaydriver']: 
                        register_plugin_types(node_desc)
                        continue
                    
                    typename, nodetype = generate_node_type(node_desc, is_oso=is_oso)
                    if not typename and not nodetype:
                        continue

                    if typename and nodetype:
                        __RMAN_NODE_TYPES__[typename] = nodetype

                    # categories
                    node_item = NodeItem(typename, label=nodetype.bl_label)
                    if node_desc.node_type == 'pattern':
                        # we favor the OSL version over the C++ version
                        # so only add the OSL version into categories list                        
                        if is_args:
                            args_filename = os.path.splitext(filename)[0]
                            if args_filename in [node_desc.name for node_desc_list in [nodes for cat,nodes in __RMAN_NODES__.items()] for node_desc in node_desc_list]:
                                continue                              
                        if hasattr(node_desc, 'classification'):
                            try:
                                tokens = node_desc.classification.split('/')                                
                                category = tokens[-1].lower()
                                category_nice_name = category.capitalize()
                                # category seems empty. Put in misc
                                if category == '':
                                    category = 'misc'
                                if node_desc.name not in ['PxrLayer', 'PxrLayerMixer']:
                                    # append OSL to the category if these are osl shaders, except
                                    # for PxrLayer and PxrLayerMixer
                                    if is_oso:
                                        pass
                                        #category_nice_name = 'OSL %s' % category.capitalize()
                                        #category = 'OSL_%s' % category                                        
                                lst = __RMAN_NODE_CATEGORIES__['pattern'].get('patterns_%s' % category, None)
                                if not lst:
                                    lst = (('RenderMan %s Patterns' % category_nice_name, []), [])
                                lst[0][1].append(node_item)
                                lst[1].append(node_desc)
                                __RMAN_NODE_CATEGORIES__['pattern']['patterns_%s' % category] = lst                                         
                            except Exception as e:
                                pass
                        else:
                            __RMAN_NODE_CATEGORIES__['pattern']['patterns_misc'][0][1].append(node_item)
                            __RMAN_NODE_CATEGORIES__['pattern']['patterns_misc'][1].append(node_desc)
                    elif node_desc.node_type == 'bxdf':
                        if hasattr(node_desc, 'classification'):
                            classification = node_desc.classification
                            tokens = classification.split(':')
                            category = ''
                            
                            # first, find rendernode
                            for token in tokens:
                                if token.startswith('rendernode'):
                                    category = token
                                    continue
                            # if we didn't find anything, put this into the misc. cateogry   
                            if category == '' or ('main' not in category):
                                __RMAN_NODE_CATEGORIES__['bxdf']['bxdf_misc'][0][1].append(node_item)
                                __RMAN_NODE_CATEGORIES__['bxdf']['bxdf_misc'][1].append(node_desc)
                                continue
                           
                            # now, split on /, and look for main
                            tokens = category.split('/')
                            i = 0
                            for i,token in enumerate(tokens):
                                if token == 'main':
                                    break

                            # if there are more tokens after "main", let's
                            # not include main in the category name
                            if i+1 < len(tokens):
                                i += 1

                            category = '_'.join(tokens[i:])
                            category_nice_name = ''
                            for token in tokens[i:]:
                                category_nice_name += token.capitalize() + ' '
                            lst = __RMAN_NODE_CATEGORIES__['bxdf'].get('bxdf_%s' % category, None)
                            if not lst:
                                lst = (('RenderMan %s Bxdf' % category_nice_name, []), [])
                            lst[0][1].append(node_item)
                            lst[1].append(node_desc)
                            __RMAN_NODE_CATEGORIES__['bxdf']['bxdf_%s' % category] = lst    
                        else:
                            __RMAN_NODE_CATEGORIES__['bxdf']['bxdf_misc'][0][1].append(node_item)
                            __RMAN_NODE_CATEGORIES__['bxdf']['bxdf_misc'][1].append(node_desc)
                    elif node_desc.node_type == 'displace':
                        __RMAN_NODE_CATEGORIES__['displace']['displace'][0][1].append(node_item)
                        __RMAN_NODE_CATEGORIES__['displace']['displace'][1].append(node_desc)
                    elif node_desc.node_type == 'light':
                        __RMAN_NODE_CATEGORIES__['light']['light'][0][1].append(node_item)
                        __RMAN_NODE_CATEGORIES__['light']['light'][1].append(node_desc)     
                    elif node_desc.node_type == 'samplefilter':
                        __RMAN_NODE_CATEGORIES__['samplefilter']['samplefilter'][0][1].append(node_item)      
                        __RMAN_NODE_CATEGORIES__['samplefilter']['samplefilter'][1].append(node_desc)      
                    elif node_desc.node_type == 'displayfilter':
                        __RMAN_NODE_CATEGORIES__['displayfilter']['displayfilter'][0][1].append(node_item)
                        __RMAN_NODE_CATEGORIES__['displayfilter']['displayfilter'][1].append(node_desc)                                                                
                    elif node_desc.node_type == 'integrator':
                        __RMAN_NODE_CATEGORIES__['integrator']['integrator'][0][1].append(node_item)  
                        __RMAN_NODE_CATEGORIES__['integrator']['integrator'][1].append(node_desc)        

    rfb_log().debug("Finished Registering RenderMan Plugin Nodes.")


def register_node_categories():

    # all categories in a list
    node_categories = []
    for k,v in __RMAN_NODE_CATEGORIES__.items():
        for name, ((desc, items), lst) in v.items():
            if items:
                node_categories.append(RendermanShaderNodeCategory(name, desc,
                                                                    items=sorted(items,
                                                                                key=attrgetter('_label'))))    

    nodeitems_utils.register_node_categories("RENDERMANSHADERNODES",
                                             node_categories)    

def register():
    global __RMAN_NODES_ALREADY_REGISTERED__
    if not __RMAN_NODES_ALREADY_REGISTERED__:
        register_rman_nodes()
        __RMAN_NODES_ALREADY_REGISTERED__ = True    
    register_node_categories()
    rman_bl_nodes_props.register()
    rman_bl_nodes_sockets.register()
    rman_bl_nodes_shaders.register()
    rman_bl_nodes_ops.register()
    rman_bl_nodes_menus.register()

def unregister():
    nodeitems_utils.unregister_node_categories("RENDERMANSHADERNODES")

    rman_bl_nodes_props.unregister()
    rman_bl_nodes_sockets.unregister()    
    rman_bl_nodes_shaders.unregister()
    rman_bl_nodes_ops.unregister()  
    rman_bl_nodes_menus.unregister()