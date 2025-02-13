# utils
from .rfb_utils import object_utils
from .rfb_utils import transform_utils
from .rfb_utils import texture_utils
from .rfb_utils import scene_utils
from .rfb_utils import shadergraph_utils

from .rfb_logger import rfb_log
from .rman_sg_nodes.rman_sg_lightfilter import RmanSgLightFilter

from . import rman_constants
import bpy

class RmanSceneSync(object):
    '''
    The RmanSceneSync class handles keeping the RmanScene object in sync
    during IPR. 

    Attributes:
        rman_render (RmanRender) - pointer back to the current RmanRender object
        rman () - rman python module
        rman_scene (RmanScene) - pointer to the current RmanScene object
        sg_scene (RixSGSCene) - the RenderMan scene graph object

    '''

    def __init__(self, rman_render=None, rman_scene=None, sg_scene=None):
        self.rman_render = rman_render
        self.rman = rman_render.rman
        self.rman_scene = rman_scene
        self.sg_scene = sg_scene        

        self.new_objects = set() # set of objects that were added to the scene
        self.new_cameras = set() # set of new camera objects that were added to the scene
        self.update_instances = set() # set of objects we need to update their instances
        self.update_particles = set() # set of objects we need to update their particle systemd
        self.do_delete = False # whether or not we need to do an object deletion
        self.do_add = False # whether or not we need to add an object
        self.num_instances_changed = False # if the number of instances has changed since the last update

    @property
    def sg_scene(self):
        return self.__sg_scene

    @sg_scene.setter
    def sg_scene(self, sg_scene):
        self.__sg_scene = sg_scene          

    def update_view(self, context, depsgraph):
        camera = depsgraph.scene.camera
        self.rman_scene.context = context
        self.rman_scene.depsgraph = depsgraph
        self.rman_scene.bl_scene = depsgraph.scene_eval
        rman_sg_camera = self.rman_scene.main_camera
        translator = self.rman_scene.rman_translators['CAMERA']
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            if self.rman_scene.is_viewport_render:
                ob = translator.update_viewport_resolution(rman_sg_camera)
                if ob:
                    translator.update_viewport_cam(ob, rman_sg_camera, force_update=True)
                translator.update_transform(None, rman_sg_camera)
            else:
                translator.update_transform(camera, rman_sg_camera)  

    def _scene_updated(self):

        # Check changes to local view
        if self.rman_scene.bl_local_view and (self.rman_scene.context.space_data.local_view is None):
            self.rman_scene.bl_local_view = False
            for ob in self.rman_scene.bl_scene.objects:
                if ob.type in ('ARMATURE', 'CURVE', 'CAMERA', 'LIGHT'):
                    continue
                self.clear_instances(ob)
                self.update_instances.add(ob.original)
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):         
                self.rman_scene.check_solo_light()
        elif not self.rman_scene.bl_local_view and (self.rman_scene.context.space_data.local_view is not None):
            self.rman_scene.bl_local_view = True   
            for ob in self.rman_scene.bl_scene.objects:
                if ob.type in ('ARMATURE', 'CURVE', 'CAMERA', 'LIGHT'):
                    continue
                self.clear_instances(ob)               
                self.update_instances.add(ob.original)
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):                     
                self.rman_scene.check_solo_light()  

        # Check view_layer
        view_layer = self.rman_scene.depsgraph.view_layer
        if len(view_layer.objects) != self.rman_scene.num_objects_in_viewlayer:
            # objects can be removed from the viewlayer by hiding a collection. 
            # Figure out the difference using sets and re-emit their instances.
            self.rman_scene.num_objects_in_viewlayer = len(view_layer.objects)
            view_layer = self.rman_scene.depsgraph.view_layer
            set1 = set(self.rman_scene.objects_in_viewlayer)
            set2 =  set((view_layer.objects))
            set_diff1 = set1.difference(set2)
            set_diff2 = set2.difference(set1)

            objects = list(set_diff1.union(set_diff2))           
            for o in list(objects):
                try:
                    self.update_instances.add(o.original)
                    self.clear_instances(o)
                    self.update_particles.add(o)  
                    self.update_geometry_node_instances(o)     
                except:
                    continue

        self.rman_scene.objects_in_viewlayer = [o for o in view_layer.objects]            

        if self.rman_scene.bl_frame_current != self.rman_scene.bl_scene.frame_current:
            # frame changed, update any materials and objects that 
            # are marked as frame sensitive
            rfb_log().debug("Frame changed: %d -> %d" % (self.rman_scene.bl_frame_current, self.rman_scene.bl_scene.frame_current))
            self.rman_scene.bl_frame_current = self.rman_scene.bl_scene.frame_current
            material_translator = self.rman_scene.rman_translators["MATERIAL"]

            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):  
                # update frame number
                options = self.rman_scene.sg_scene.GetOptions()
                options.SetInteger(self.rman.Tokens.Rix.k_Ri_Frame, self.rman_scene.bl_frame_current)
                self.rman_scene.sg_scene.SetOptions(options)        

                for mat in bpy.data.materials:   
                    db_name = object_utils.get_db_name(mat)  
                    rman_sg_material = self.rman_scene.rman_materials.get(mat.original, None)
                    if rman_sg_material and rman_sg_material.is_frame_sensitive:
                        material_translator.update(mat, rman_sg_material)

                for o in bpy.data.objects:
                    rman_type = object_utils._detect_primitive_(o)
                    rman_sg_node = self.rman_scene.rman_objects.get(o.original, None)
                    if not rman_sg_node:
                        continue
                    translator = self.rman_scene.rman_translators.get(rman_type, None)
                    if translator and rman_sg_node.is_frame_sensitive:
                        translator.update(o, rman_sg_node)                   

    def _mesh_light_update(self, mat):
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            for ob_inst in self.rman_scene.depsgraph.object_instances:
                psys = None
                if ob_inst.is_instance:
                    ob = ob_inst.instance_object
                    group_db_name =  object_utils.get_group_db_name(ob_inst)
                else:
                    ob = ob_inst.object
                    group_db_name =  object_utils.get_group_db_name(ob_inst)
                if not hasattr(ob.data, 'materials'):
                    continue   
                if ob.type in ('ARMATURE', 'CURVE', 'CAMERA'):
                    continue                         
                rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)
                if rman_sg_node:
                    found = False
                    for name, material in ob.data.materials.items():
                        if name == mat.name:
                            found = True

                    if found:
                        rman_sg_group = rman_sg_node.instances.get(group_db_name, None)
                        if rman_sg_group:
                            rman_sg_node.instances.pop(group_db_name)
                            self.rman_scene.sg_scene.DeleteDagNode(rman_sg_group.sg_node)                              
                            self.rman_scene._export_instance(ob_inst)                 

    def _material_updated(self, obj):
        mat = obj.id
        rman_sg_material = self.rman_scene.rman_materials.get(mat.original, None)
        translator = self.rman_scene.rman_translators["MATERIAL"]         
        db_name = object_utils.get_db_name(mat)
        if not rman_sg_material:
            # Double check if we can't find the material because of an undo
            rman_sg_material = self.update_materials_dict(mat)

        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):   
            mat = obj.id              
            if not rman_sg_material:
                rfb_log().debug("New material: %s" % mat.name)
                db_name = object_utils.get_db_name(mat)
                rman_sg_material = translator.export(mat, db_name)
                self.rman_scene.rman_materials[mat.original] = rman_sg_material            
            else:
                rfb_log().debug("Material, call update")
                translator.update(mat, rman_sg_material)   

        # update db_name
        rman_sg_material.db_name = db_name

    def _light_filter_transform_updated(self, obj):
        ob = obj.id
        rman_sg_lightfilter = self.rman_scene.rman_objects.get(ob.original, None)
        if rman_sg_lightfilter:
            rman_group_translator = self.rman_scene.rman_translators['GROUP']  
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):              
                rman_group_translator.update_transform(ob, rman_sg_lightfilter)

    def _gpencil_transform_updated(self, obj):
        ob = obj.id
        rman_sg_gpencil = self.rman_scene.rman_objects.get(ob.original, None)
        if rman_sg_gpencil:
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):       
                rman_group_translator = self.rman_scene.rman_translators['GROUP']         
                for ob_inst in self.rman_scene.depsgraph.object_instances: 
                    group_db_name = object_utils.get_group_db_name(ob_inst)
                    rman_sg_group = rman_sg_gpencil.instances.get(group_db_name, None)
                    if rman_sg_group:
                        rman_group_translator.update_transform(ob, rman_sg_group)                

    def _obj_geometry_updated(self, obj):
        ob = obj.id
        rman_type = object_utils._detect_primitive_(ob)
        db_name = object_utils.get_db_name(ob, rman_type=rman_type) 
        rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)

        if rman_type in ['LIGHT', 'LIGHTFILTER', 'CAMERA']:
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                if rman_type == 'LIGHTFILTER':
                    self.rman_scene.rman_translators['LIGHTFILTER'].update(ob, rman_sg_node)
                    for light_ob in rman_sg_node.lights_list:
                        if isinstance(light_ob, bpy.types.Material):
                            rman_sg_material = self.rman_scene.rman_materials.get(light_ob.original, None)
                            if rman_sg_material:
                                self.rman_scene.rman_translators['MATERIAL'].update_light_filters(light_ob, rman_sg_material)                      
                        else:
                            rman_sg_light = self.rman_scene.rman_objects.get(light_ob.original, None)
                            if rman_sg_light:
                                self.rman_scene.rman_translators['LIGHT'].update_light_filters(light_ob, rman_sg_light)                      

                elif rman_type == 'LIGHT':
                    self.rman_scene.rman_translators['LIGHT'].update(ob, rman_sg_node)
                                                        
                    if not self.rman_scene.scene_solo_light:
                        # only set if a solo light hasn't been set
                        if not self.rman_scene.check_light_local_view(ob, rman_sg_node):
                            rman_sg_node.sg_node.SetHidden(ob.data.renderman.mute)
                elif rman_type == 'CAMERA':
                    ob = ob.original
                    rman_camera_translator = self.rman_scene.rman_translators['CAMERA']
                    if not self.rman_scene.is_viewport_render:
                        rman_camera_translator.update(ob, rman_sg_node)  
                    else:
                        rman_camera_translator.update_viewport_cam(ob, rman_sg_node, force_update=True)       

        else:
            if rman_sg_node.rman_type != rman_type:
                # for now, we don't allow the rman_type to be changed
                rfb_log().error("Changing primitive type is currently not supported.")
                return
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):     
                translator = self.rman_scene.rman_translators.get(rman_type, None)
                if not translator:
                    return
                translator.update(ob, rman_sg_node)
                translator.export_object_primvars(ob, rman_sg_node)
                # material slots could have changed, so we need to double
                # check that too
                for k,v in rman_sg_node.instances.items():
                    self.rman_scene.attach_material(ob, v)

                if rman_sg_node.sg_node:
                    if not ob.show_instancer_for_viewport:
                        rman_sg_node.sg_node.SetHidden(1)
                    else:
                        rman_sg_node.sg_node.SetHidden(-1)

    def update_light_visibility(self, rman_sg_node, ob):
        if not self.rman_scene.scene_solo_light:
            vis = rman_sg_node.sg_node.GetHidden()
            if vis == -1:
                vis = 0
            result = False
            update_instances = False
            # if vis is inherit, and none of the other visibility attrs are set to hide
            if vis == -1 and not ob.hide_get() and int(ob.renderman.mute) == 0:
                update_instances = True
                result = False
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                if self.rman_scene.check_light_local_view(ob, rman_sg_node):
                    update_instances = True
                    result = True
                elif not ob.hide_get():
                    rman_sg_node.sg_node.SetHidden(ob.renderman.mute)
                    update_instances = True
                    result = (vis != int(ob.renderman.mute))
                else:
                    rman_sg_node.sg_node.SetHidden(1)
                    result = (vis != 1)

            if update_instances and len(rman_sg_node.instances) < 1:
                self.update_instances.add(ob.original)
            return result

    def update_object_visibility(self, rman_sg_node, ob):                
        ob_data = bpy.data.objects.get(ob.name, ob)
        rman_type = object_utils._detect_primitive_(ob_data)
        particle_systems = getattr(ob_data, 'particle_systems', list())
        has_particle_systems = len(particle_systems) > 0
        is_hidden = ob_data.hide_get()

        # double check hidden value
        if rman_type in ['LIGHT']:
            if self.update_light_visibility(rman_sg_node, ob):
                rfb_log().debug("Update light visibility: %s" % ob.name)
                return True
        else:
            if rman_sg_node.is_hidden != is_hidden:
                self.do_delete = False
                rman_sg_node.is_hidden = is_hidden
                if rman_type == 'EMPTY':
                    self.update_empty(ob, rman_sg_node)
                else:         
                    self.update_instances.add(ob.original) 
                    self.clear_instances(ob, rman_sg_node)      
                    if has_particle_systems:                                     
                        self.update_particles.add(ob.original)  
                return True
        return False      
                
    def update_particle_settings(self, obj, particle_settings_node):
        rfb_log().debug("Check %s for particle settings." % obj.id.name)
        # A ParticleSettings node was updated. Try to look for it.
        ob = obj.id
        rman_type = object_utils._detect_primitive_(ob)
        for psys in obj.id.particle_systems:
            if psys.settings.original == particle_settings_node:
                if psys.settings.type == 'FLIP' and rman_type == 'FLUID':
                    fluid_translator = self.rman_scene.rman_translators['FLUID']
                    rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)
                    with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                        fluid_translator.update(ob, rman_sg_node)
                    return

                ob_psys = self.rman_scene.rman_particles.get(obj.id.original, dict())
                rman_sg_particles = ob_psys.get(psys.settings.original, None)
                if rman_sg_particles:
                    with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                        psys_translator = self.rman_scene.rman_translators['PARTICLES']
                        psys_translator.update(obj.id, psys, rman_sg_particles)
                    return
                # This is a particle instancer. The instanced object needs to updated
                elif object_utils.is_particle_instancer(psys):
                    inst_object = getattr(particle_settings_node, 'instance_object', None) 
                    collection = getattr(particle_settings_node, 'instance_collection', None)
                    if inst_object:
                        self.update_instances.add(inst_object.original)
                    if collection:
                        for col_obj in collection.all_objects:
                            if col_obj.original not in self.rman_scene.rman_objects:
                                self.new_objects.add(col_obj.original)
                            self.update_instances.add(col_obj.original) 
                    break                                 

        # Update any other instance objects this object instanced. The instanced
        # object may have changed
        rman_sg_node = self.rman_scene.rman_objects.get(obj.id.original, None)
        for instance_obj in rman_sg_node.objects_instanced:
            self.clear_instances(instance_obj)
            self.update_instances.add(instance_obj)   

    def update_particle_systems(self):

        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):       
            for ob in self.update_particles:
                rman_type = object_utils._detect_primitive_(ob)   
                if rman_type not in ['MESH', 'POINTS']:    
                    continue
                rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)
                ob_eval = ob.evaluated_get(self.rman_scene.depsgraph)
                rfb_log().debug("Update  particle systems for: %s" % ob.name)

                # any objects that this object instanced, need to update their instances
                for instance_obj in rman_sg_node.objects_instanced:
                    self.clear_instances(instance_obj)
                    self.update_instances.add(instance_obj)                

                if rman_sg_node.rman_sg_particle_group_node:
                    rman_sg_node.rman_sg_particle_group_node.sg_node.RemoveAllChildren()

                if len(ob_eval.particle_systems) < 1:
                    continue                
                
                if not rman_sg_node.rman_sg_particle_group_node:
                    db_name = rman_sg_node.db_name
                    particles_group_db = ''
                    rman_sg_node.rman_sg_particle_group_node = self.rman_scene.rman_translators['GROUP'].export(None, particles_group_db) 
                    rman_sg_node.sg_node.AddChild(rman_sg_node.rman_sg_particle_group_node.sg_node) 

                psys_translator = self.rman_scene.rman_translators['PARTICLES']

                for psys in ob_eval.particle_systems:
                    if object_utils.is_particle_instancer(psys):
                        # this particle system is a instancer, add the instanced object
                        # to the self.update_instances list
                        inst_ob = getattr(psys.settings, 'instance_object', None) 
                        collection = getattr(psys.settings, 'instance_collection', None)
                        if inst_ob:
                            self.update_instances.add(inst_ob.original)      
                            rman_instance_sg_node = self.rman_scene.rman_objects.get(inst_ob.original, None)
                            if rman_instance_sg_node:
                                self.clear_instances(inst_ob.original, rman_instance_sg_node)
                        elif collection:
                            for col_obj in collection.all_objects:
                                self.update_instances.add(col_obj.original) 
                                rman_instance_sg_node = self.rman_scene.rman_objects.get(col_obj.original, None)
                                if rman_instance_sg_node:
                                    self.clear_instances(col_obj.original, rman_instance_sg_node)
                                else:
                                    self.new_objects.add(col_obj.original)                       
                        continue

                    ob_psys = self.rman_scene.rman_particles.get(ob_eval.original, dict())
                    rman_sg_particles = ob_psys.get(psys.settings.original, None)
                    if not rman_sg_particles:
                        psys_db_name = '%s' % psys.name
                        rman_sg_particles = psys_translator.export(ob, psys, psys_db_name)
                        if not rman_sg_particles:
                            continue
                    psys_translator.update(ob, psys, rman_sg_particles)
                    ob_psys[psys.settings.original] = rman_sg_particles
                    self.rman_scene.rman_particles[ob.original] = ob_psys          
                    rman_sg_node.rman_sg_particle_group_node.sg_node.AddChild(rman_sg_particles.sg_node)    

    def update_empty(self, ob, rman_sg_node=None):
        rfb_log().debug("Update empty: %s" % ob.name)
        if ob.is_instancer:            
            collection = ob.instance_collection
            if collection:
                if self.num_instances_changed:
                    for col_obj in collection.all_objects:
                        self.update_instances.add(col_obj.original) 
                        rman_instance_sg_node = self.rman_scene.rman_objects.get(col_obj.original, None)
                        if rman_instance_sg_node:
                            self.clear_instances(col_obj.original, rman_instance_sg_node)                            
                        else:
                            self.new_objects.add(col_obj.original)                            
                        self.update_particles.add(col_obj)
                else:
                    for col_obj in collection.all_objects:
                        self.update_instances.add(col_obj.original)     
                        self.update_particles.add(col_obj)
    
        else:
            translator = self.rman_scene.rman_translators['EMPTY']
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                translator.export_transform(ob, rman_sg_node.sg_node)
                if ob.renderman.export_as_coordsys:
                    self.rman_scene.get_root_sg_node().AddCoordinateSystem(rman_sg_node.sg_node)
                else:
                    self.rman_scene.get_root_sg_node().RemoveCoordinateSystem(rman_sg_node.sg_node)                       

    def reemit_instances(self):    
        # update instances
        if not self.update_instances:
            return
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            # Re-emit instances for all objects in self.update_instances
            rfb_log().debug("Re-emit instances")
            rman_group_translator = self.rman_scene.rman_translators['GROUP']
            for ob_inst in self.rman_scene.depsgraph.object_instances: 
                parent = None
                if ob_inst.is_instance:
                    ob = ob_inst.instance_object
                    parent = ob_inst.parent
                else:
                    ob = ob_inst.object

                if ob.original not in self.update_instances:
                    continue

                rman_type = object_utils._detect_primitive_(ob)
                rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)
                if rman_sg_node:
                    translator = self.rman_scene.rman_translators.get(rman_type, None)
                    translator.export_object_primvars(ob, rman_sg_node)

                    group_db_name = object_utils.get_group_db_name(ob_inst) 
                    rman_sg_group = rman_sg_node.instances.get(group_db_name, None)
                    if rman_sg_group:
                        rman_group_translator.update_transform(ob_inst, rman_sg_group)
                        # object attrs             
                        rman_group_translator.export_object_attributes(ob, rman_sg_group)  
                        if rman_sg_group.bl_psys_settings:
                            self.rman_scene.attach_particle_material(rman_sg_group.bl_psys_settings, parent, ob, rman_sg_group)
                        else:
                            self.rman_scene.attach_material(ob, rman_sg_group)
                        continue                    
                
                self.rman_scene._export_instance(ob_inst)            

    def clear_instances(self, ob, rman_sg_node=None):
        rfb_log().debug("Deleting instances")
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            if not rman_sg_node:
                rman_sg_node = self.rman_scene.rman_objects.get(ob.original)
            for k,rman_sg_group in rman_sg_node.instances.items():
                if ob.parent and object_utils._detect_primitive_(ob.parent) == 'EMPTY':
                    rman_empty_node = self.rman_scene.rman_objects.get(ob.parent.original)
                    rman_empty_node.sg_node.RemoveChild(rman_sg_group.sg_node)
                else:
                    self.rman_scene.get_root_sg_node().RemoveChild(rman_sg_group.sg_node)                            
            rman_sg_node.instances.clear()      

    def update_materials_dict(self, mat):    
        # See comment below in update_objects_dict 
        rman_sg_material = None
        for id, rman_sg_node in self.rman_scene.rman_materials.items():
            if rman_sg_node:
                db_name = object_utils.get_db_name(mat)
                if rman_sg_node.db_name == db_name:
                    self.rman_scene.rman_materials[mat.original] = rman_sg_node
                    del self.rman_scene.rman_materials[id]
                    rman_sg_material = rman_sg_node 
                    break
        
        return rman_sg_material

    def update_objects_dict(self, ob, rman_type=None):      
        # Try to see if we already have an obj with the same db_name
        # We need to do this because undo/redo causes all bpy.types.ID 
        # references to be invalidated (see: https://docs.blender.org/api/current/info_gotcha.html)
        # We don't want to accidentally mistake this for a new object, so we need to update
        # our objects dictionary with the new bpy.types.ID reference
        rman_sg_node = None
        for id, rsn in self.rman_scene.rman_objects.items():
            if rsn:
                db_name = object_utils.get_db_name(ob, rman_type=rman_type)
                if rsn.db_name == db_name:
                    self.rman_scene.rman_objects[ob.original] = rsn
                    del self.rman_scene.rman_objects[id]
                    if id in self.rman_scene.rman_cameras:
                        self.rman_scene.rman_cameras[ob.original] = rsn
                        del self.rman_scene.rman_cameras[id]
                    rman_sg_node = rsn
                    break
        return rman_sg_node

    def update_collection(self, coll):
        # mark all objects in a collection
        # as needing their instances updated
        # the collection could have been updated with new objects
        # FIXME: like grease pencil above we seem to crash when removing and adding instances 
        # of curves, we need to figure out what's going on
        for o in coll.all_objects:
            if o.type in ('ARMATURE', 'CURVE', 'CAMERA'):
                continue

            rman_type = object_utils._detect_primitive_(o)
            rman_sg_node = self.rman_scene.rman_objects.get(o.original, None)
            if not rman_sg_node:
                if not self.update_objects_dict(o, rman_type=rman_type):
                    self.new_objects.add(o)
                    self.update_instances.add(o)
                    continue

            if rman_type == 'LIGHT':
                # Check light visibility. Light visibility is already handled elsewhere
                with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene): 
                    if self.rman_scene.check_light_local_view(o, rman_sg_node):
                        continue

            self.update_instances.add(o.original)
            self.update_particles.add(o)  
            self.update_geometry_node_instances(o) 

    def update_geometry_node_instances(self, obj):
        def update_geo_instances(nodes):
            # look for all point instance nodes
            for n in [node for node in nodes if isinstance(node, bpy.types.GeometryNodePointInstance)]:
                if n.instance_type == 'OBJECT':
                    instance_obj = n.inputs['Object'].default_value
                    if instance_obj:
                        self.clear_instances(instance_obj)
                        self.update_particles.add(instance_obj)                        
                        self.update_instances.add(instance_obj.original)
                elif n.instance_type == 'COLLECTION':
                    instance_coll = n.inputs['Collection'].default_value
                    if instance_coll:
                        self.update_collection(instance_coll)                


        if rman_constants.BLENDER_VERSION_MAJOR >= 2 and rman_constants.BLENDER_VERSION_MINOR >= 92:
            if isinstance(obj, bpy.types.GeometryNodeTree):
                rfb_log().debug("Geometry Node Tree updated: %s" % obj.name)
                # look for all point instance nodes
                update_geo_instances(obj.nodes)     
            elif hasattr(obj, 'modifiers'):
                # This is an object with modifiers. Look for any geometry node trees attached.
                node_tree = None
                for modifier in obj.modifiers:
                    if modifier.type == 'NODES':
                        rfb_log().debug("Geometry Node Tree updated: %s" % modifier.node_group.name)
                        update_geo_instances(modifier.node_group.nodes)

    def update_portals(self, ob):
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            translator = self.rman_scene.rman_translators['LIGHT']
            for portal in scene_utils.get_all_portals(ob):
                rman_sg_node = self.rman_scene.rman_objects.get(portal.original, None)
                if rman_sg_node:
                    translator.update(portal, rman_sg_node)


    def update_scene(self, context, depsgraph):
        ## FIXME: this function is waaayyy too big and is doing too much stuff

        self.new_objects.clear() 
        self.new_cameras.clear()
        self.update_instances.clear()
        self.update_particles.clear()

        self.do_delete = False # whether or not we need to do an object deletion
        self.do_add = False # whether or not we need to add an object
        self.num_instances_changed = False # if the number of instances has changed since the last update
                
        self.rman_scene.depsgraph = depsgraph
        self.rman_scene.bl_scene = depsgraph.scene
        self.rman_scene.context = context           

        particle_settings_node = None   
        prev_num_instances = self.rman_scene.num_object_instances # the number of instances previously
        
        # Check the number of instances. If we differ, an object may have been
        # added or deleted
        if self.rman_scene.num_object_instances != len(depsgraph.object_instances):
            self.num_instances_changed = True
            if self.rman_scene.num_object_instances > len(depsgraph.object_instances):
                self.do_delete = True
            else:
                self.do_add = True
            self.rman_scene.num_object_instances = len(depsgraph.object_instances)

        rfb_log().debug("------Start update scene--------")
        for obj in reversed(depsgraph.updates):
            ob = obj.id

            if isinstance(obj.id, bpy.types.Scene):
                self._scene_updated()

            elif isinstance(obj.id, bpy.types.World):
                with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene): 
                    self.rman_scene.export_integrator()
                    self.rman_scene.export_samplefilters()
                    self.rman_scene.export_displayfilters()
                    self.rman_scene.export_viewport_stats()

            elif isinstance(obj.id, bpy.types.Camera):
                rfb_log().debug("Camera updated: %s" % obj.id.name)
                if self.rman_scene.is_viewport_render:
                    if self.rman_scene.bl_scene.camera.data != obj.id:
                        continue
                    rman_sg_camera = self.rman_scene.main_camera
                    translator = self.rman_scene.rman_translators['CAMERA']
                    with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                        translator.update_viewport_cam(self.rman_scene.bl_scene.camera, rman_sg_camera, force_update=True)       
                else:
                    translator = self.rman_scene.rman_translators['CAMERA']                 
                    with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                        for ob, rman_sg_camera in self.rman_scene.rman_cameras.items():     
                            if ob.original.name != obj.id.name:
                                continue
                            translator._update_render_cam(ob.original, rman_sg_camera)

            elif isinstance(obj.id, bpy.types.Material):
                rfb_log().debug("Material updated: %s" % obj.id.name)
                self._material_updated(obj)    

            elif isinstance(obj.id, bpy.types.Mesh):
                rfb_log().debug("Mesh updated: %s" % obj.id.name)
                '''
                # Experimental code path. We can use context.blend_data.user_map to ask
                # what objects use this mesh. We can then loop thru and call object_update on these
                # objects.
                # We could also try doing the same thing when we add a new Material. i.e.:
                # use user_map to figure out what objects are using this material; however, that would require
                # two loops thru user_map
                users = context.blend_data.user_map(subset={obj.id.original}, value_types={'OBJECT'})
                translator = self.rman_scene.rman_translators['MESH']
                with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                    for o in users[obj.id.original]:
                        rman_type = object_utils._detect_primitive_(o)
                        if rman_type != 'MESH':
                            continue
                        rman_sg_node = self.rman_scene.rman_objects.get(o.original, None)
                        translator.update(o, rman_sg_node)
                        translator.export_object_primvars(o, rman_sg_node)
                        # material slots could have changed, so we need to double
                        # check that too
                        for k,v in rman_sg_node.instances.items():
                            self.rman_scene.attach_material(o, v)                
                return
                '''

            elif isinstance(obj.id, bpy.types.ParticleSettings):
                rfb_log().debug("ParticleSettings updated: %s" % obj.id.name)
                # Save this particle settings node, so we can check for it later 
                # when we process object changes
                particle_settings_node = obj.id.original

            elif isinstance(obj.id, bpy.types.ShaderNodeTree):
                if obj.id.name in bpy.data.node_groups:
                    # this is probably one of our fake node groups with ramps
                    # update all of the users of this node tree
                    rfb_log().debug("ShaderNodeTree updated: %s" % obj.id.name)
                    users = context.blend_data.user_map(subset={obj.id.original})
                    for o in users[obj.id.original]:
                        if hasattr(o, 'rman_nodetree'):
                            o.rman_nodetree.update_tag()
                        elif hasattr(o, 'node_tree'):
                            o.node_tree.update_tag()                
                                            
            elif isinstance(obj.id, bpy.types.Object):
                particle_systems = getattr(obj.id, 'particle_systems', list())
                has_particle_systems = len(particle_systems) > 0

                rman_type = object_utils._detect_primitive_(ob)
                # grab the object from bpy.data, because the depsgraph doesn't seem
                # to get the updated viewport hidden value                
                ob_data = bpy.data.objects.get(ob.name, ob)
                rman_sg_node = self.rman_scene.rman_objects.get(obj.id.original, None)
                
                # NOTE: hide_get() and hide_viewport are two different things in Blender
                # hide_get() hides the object from the viewport, but it does not actually remove the object
                # as instances of the object can still be visible (ex: in particle systems)
                # hide_viewport should be interpreted as an actual deleted object, including
                # particle instances.
                is_hidden = ob_data.hide_get()

                if not rman_sg_node:
                    rman_sg_node = self.update_objects_dict(obj.id, rman_type=rman_type)
                                
                if self.do_add and not rman_sg_node:
                    rman_type = object_utils._detect_primitive_(ob_data)

                    if ob_data.hide_get():
                        # don't add if this hidden in the viewport
                        continue                    
                    if ob.type == 'CAMERA': 
                        self.new_cameras.add(obj.id.original)
                    else:
                        if rman_type == 'EMPTY' and ob.is_instancer:
                            self.update_empty(ob)
                        else:
                            if rman_type == 'LIGHT':
                                # double check if this light is an rman light
                                # for now, we don't support adding Blender lights in IPR
                                #
                                # we can also get to this point when adding new rman lights because
                                # blender will tell us a new light has been added before we've had to chance
                                # to modify its properties to be an rman light, so we don't want to
                                # add this light just yet.
                                if not shadergraph_utils.is_rman_light(ob):
                                    self.rman_scene.num_object_instances = prev_num_instances
                                    rfb_log().debug("------End update scene----------")
                                    return
                            elif rman_type == 'EMPTY':
                                # same issue can also happen with empty
                                # we have not been able to tag our types before Blender
                                # tells us an empty has been added
                                self.rman_scene.num_object_instances = prev_num_instances
                                rfb_log().debug("------End update scene----------")
                                return
                            rfb_log().debug("New object added: %s" % obj.id.name)                           
                            self.new_objects.add(obj.id.original)
                            self.update_instances.add(obj.id.original)
                            if rman_type == 'LIGHTFILTER':
                                # Add Light filters immediately, so that lights
                                # can reference them ASAP.
                                self.add_objects()
                                self.new_objects.remove(obj.id.original)   
                                self.num_instances_changed = False
                    continue      

                if rman_sg_node and rman_sg_node.sg_node:
                    # update db_name
                    db_name = object_utils.get_db_name(ob, rman_type=rman_type)
                    rman_sg_node.db_name = db_name
                    if self.update_object_visibility(rman_sg_node, ob):
                        continue
                else:
                    continue        

                if obj.is_updated_transform:
                    rfb_log().debug("Transform updated: %s" % obj.id.name)                  
                    if ob.type in ['CAMERA']:
                        # we deal with main camera transforms in view_draw
                        rman_sg_camera = self.rman_scene.rman_cameras[ob.original]
                        if rman_sg_camera == self.rman_scene.main_camera:
                            continue
                        translator = self.rman_scene.rman_translators['CAMERA']
                        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                            translator._update_render_cam_transform(ob, rman_sg_camera)                        
                        continue
                    
                    if rman_type == 'LIGHTFILTER':
                        self._light_filter_transform_updated(obj)
                    elif rman_type == 'GPENCIL':
                        # FIXME: we shouldn't handle this specifically, but we seem to be
                        # hitting a prman crash when removing and adding instances of
                        # grease pencil curves
                        self._gpencil_transform_updated(obj)
                    elif rman_type == 'EMPTY':
                        self.update_empty(ob, rman_sg_node)
                    elif self.num_instances_changed:
                        rman_sg_node = self.rman_scene.rman_objects.get(obj.id.original, None)
                        for instance_obj in rman_sg_node.objects_instanced:
                            self.clear_instances(instance_obj)
                            self.update_instances.add(instance_obj)
                        rman_sg_node.objects_instanced.clear()                            
                    else:
                        # This is a simple transform. We don't clear the instances.

                        # We always have to update particle systems when the object has transformed
                        # A transform changed can also be triggered when a particle system is removed.        
                        self.update_particles.add(obj.id)                        
                        self.update_instances.add(obj.id.original)
                        self.update_geometry_node_instances(obj.id)
                        self.do_delete = False
                        if rman_type == 'LIGHT':
                            # check if portals are attached
                            self.update_portals(obj.id.original)

                    # Check if this object is the focus object the camera. If it is
                    # we need to update the camera
                    rman_sg_camera = self.rman_scene.main_camera
                    if rman_sg_camera.rman_focus_object and rman_sg_camera.rman_focus_object == rman_sg_node:
                        translator = self.rman_scene.rman_translators['CAMERA']
                        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
                            cam_object = translator.find_scene_camera()
                            translator.update(cam_object, rman_sg_camera)

                if obj.is_updated_geometry:
                    if is_hidden:
                        # don't update if this is hidden
                        continue

                    rfb_log().debug("Object updated: %s" % obj.id.name)
                    if has_particle_systems and particle_settings_node:
                        self.do_delete = False
                        self.update_particle_settings(obj, particle_settings_node)
                    else:
                        # We always update particle systems in the non-num_instance_change case
                        # because the particle system can be pointing to a whole new particle settings
                        self.update_particles.add(obj.id)

                        if not self.num_instances_changed:
                            self._obj_geometry_updated(obj)

            elif isinstance(obj.id, bpy.types.Collection):
                # don't check the collection if we know objects
                # were added or deleted in the scene.
                if self.do_delete or self.do_add:
                    continue
                
                rfb_log().debug("Collection updated: %s" % obj.id.name)
                self.update_collection(obj.id)

            else:
                self.update_geometry_node_instances(obj.id)

        # call txmake all in case of new textures
        texture_utils.get_txmanager().txmake_all(blocking=False)       
        # add new objs:
        if self.new_objects:
            self.add_objects()
        elif self.do_add:
            # if we didn't detect any new objects, but the number of
            # instances changed, check our existing objects for object
            # deletion and/or visibility
            self.delete_objects()

        # delete any objects, if necessary    
        if self.do_delete:
            self.delete_objects()

        # update any particle systems
        self.update_particle_systems()

        # re-emit any instances needed
        self.reemit_instances()                          
                        
        rfb_log().debug("------End update scene----------")

    def add_objects(self):
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene): 
            rfb_log().debug("Adding new objects:")
            self.rman_scene.export_data_blocks(self.new_objects)

            self.rman_scene.scene_any_lights = self.rman_scene._scene_has_lights()
            if self.rman_scene.scene_any_lights:
                self.rman_scene.default_light.SetHidden(1)           

    def delete_objects(self):
        rfb_log().debug("Deleting objects")
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            keys = [k for k in self.rman_scene.rman_objects.keys()]
            for obj in keys:
                try:
                    ob = self.rman_scene.bl_scene.objects.get(obj.name_full, None)
                    # NOTE: objects that are hidden from the viewport are considered deleted
                    # objects as well
                    if ob and not ob.hide_viewport:
                        rman_sg_node = self.rman_scene.rman_objects.get(obj, None)
                        if rman_sg_node:
                            # Double check object visibility
                            self.update_object_visibility(rman_sg_node, ob)
                        continue
                except Exception as e:
                    pass
    
                rman_sg_node = self.rman_scene.rman_objects.get(obj, None)
                if rman_sg_node:                        
                    for k,v in rman_sg_node.instances.items():
                        if v.sg_node:
                            self.rman_scene.sg_scene.DeleteDagNode(v.sg_node)    
                    rman_sg_node.instances.clear()             

                    # For now, don't delete the geometry itself
                    # there may be a collection instance still referencing the geo

                    # self.rman_scene.sg_scene.DeleteDagNode(rman_sg_node.sg_node)                     
                    del self.rman_scene.rman_objects[obj]

                    # We just deleted a light filter. We need to tell all lights
                    # associated with this light filter to update
                    if isinstance(rman_sg_node, RmanSgLightFilter):
                        for light_ob in rman_sg_node.lights_list:
                            light_key = object_utils.get_db_name(light_ob, rman_type='LIGHT')
                            rman_sg_light = self.rman_scene.rman_objects.get(light_ob.original, None)
                            if rman_sg_light:
                                self.rman_scene.rman_translators['LIGHT'].update_light_filters(light_ob, rman_sg_light)                                
                    try:
                        self.rman_scene.processed_obs.remove(obj)
                    except ValueError:
                        rfb_log().debug("Obj not in self.rman_scene.processed_obs: %s")
                        pass

                if self.rman_scene.render_default_light:
                    self.rman_scene.scene_any_lights = self.rman_scene._scene_has_lights()     
                    if not self.rman_scene.scene_any_lights:
                        self.rman_scene.default_light.SetHidden(0)             

    def update_cropwindow(self, cropwindow=None):
        if not self.rman_render.rman_interactive_running:
            return
        if cropwindow:
            with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene): 
                options = self.rman_scene.sg_scene.GetOptions()
                options.SetFloatArray(self.rman_scene.rman.Tokens.Rix.k_Ri_CropWindow, cropwindow, 4)  
                self.rman_scene.sg_scene.SetOptions(options)           

    def update_integrator(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        if context:
            self.rman_scene.bl_scene = context.scene
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.export_integrator() 
            self.rman_scene.export_viewport_stats()

    def update_viewport_integrator(self, context, integrator):
        if not self.rman_render.rman_interactive_running:
            return        
        self.rman_scene.bl_scene = context.scene
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            integrator_sg = self.rman_scene.rman.SGManager.RixSGShader("Integrator", integrator, "integrator")       
            self.rman_scene.sg_scene.SetIntegrator(integrator_sg)     
            self.rman_scene.export_viewport_stats(integrator=integrator)  

    def update_viewport_res_mult(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        if not self.rman_scene.is_viewport_render:
            return         
        if context:
            self.rman_scene.context = context
            self.rman_scene.bl_scene = context.scene    
            self.rman_scene.viewport_render_res_mult = float(context.scene.renderman.viewport_render_res_mult)
        rman_sg_camera = self.rman_scene.main_camera
        translator = self.rman_scene.rman_translators['CAMERA']
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            translator.update_viewport_resolution(rman_sg_camera)
            translator.update_transform(None, rman_sg_camera)
            self.rman_scene.export_viewport_stats()                  

    def update_global_options(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        self.rman_scene.bl_scene = context.scene
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.export_global_options()            
            self.rman_scene.export_hider()
            self.rman_scene.export_viewport_stats()

    def update_root_node_func(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        self.rman_scene.bl_scene = context.scene
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.export_root_sg_node()         
 
    def update_material(self, mat):
        if not self.rman_render.rman_interactive_running:
            return        
        rman_sg_material = self.rman_scene.rman_materials.get(mat.original, None)
        if not rman_sg_material:
            return
        translator = self.rman_scene.rman_translators["MATERIAL"]     
        has_meshlight = rman_sg_material.has_meshlight   
        rfb_log().debug("Manual material update called for: %s." % mat.name)
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):                  
            translator.update(mat, rman_sg_material)

        if has_meshlight != rman_sg_material.has_meshlight:
            # we're dealing with a mesh light
            rfb_log().debug("Manually calling mesh_light_update")
            self.rman_scene.depsgraph = bpy.context.evaluated_depsgraph_get()
            self._mesh_light_update(mat)    

    def update_light(self, ob):
        if not self.rman_render.rman_interactive_running:
            return        
        rman_sg_light = self.rman_scene.rman_objects.get(ob.original, None)
        if not rman_sg_light:
            return
        translator = self.rman_scene.rman_translators["LIGHT"]        
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            translator.update(ob, rman_sg_light)         

    def update_light_filter(self, ob):
        if not self.rman_render.rman_interactive_running:
            return        
        rman_sg_node = self.rman_scene.rman_objects.get(ob.original, None)
        if not rman_sg_node:
            return

        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.rman_translators['LIGHTFILTER'].update(ob, rman_sg_node)
            for light_ob in rman_sg_node.lights_list:
                light_key = object_utils.get_db_name(light_ob, rman_type='LIGHT')
                rman_sg_light = self.rman_scene.rman_objects.get(light_ob.original, None)
                if rman_sg_light:
                    self.rman_scene.rman_translators['LIGHT'].update_light_filters(light_ob, rman_sg_light)                    

    def update_solo_light(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        # solo light has changed
        self.rman_scene.bl_scene = context.scene
        self.rman_scene.scene_solo_light = self.rman_scene.bl_scene.renderman.solo_light
                    
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):            
            for light_ob in scene_utils.get_all_lights(self.rman_scene.bl_scene, include_light_filters=False):
                rman_sg_node = self.rman_scene.rman_objects.get(light_ob.original, None)
                if not rman_sg_node:
                    continue
                rm = light_ob.renderman
                if not rm:
                    continue

                if rm.solo:
                    rman_sg_node.sg_node.SetHidden(0)
                else:
                    rman_sg_node.sg_node.SetHidden(1)  

    def update_un_solo_light(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        # solo light has changed
        self.rman_scene.bl_scene = context.scene
        self.rman_scene.scene_solo_light = self.rman_scene.bl_scene.renderman.solo_light
                    
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):                                               
            for light_ob in scene_utils.get_all_lights(self.rman_scene.bl_scene, include_light_filters=False):
                rman_sg_node = self.rman_scene.rman_objects.get(light_ob.original, None)
                if not rman_sg_node:
                    continue
                rm = light_ob.renderman
                if not rm:
                    continue         
                if self.rman_scene.check_light_local_view(light_ob, rman_sg_node):
                    continue
                rman_sg_node.sg_node.SetHidden(light_ob.hide_get())         

    def update_viewport_chan(self, context, chan_name):
        if not self.rman_render.rman_interactive_running:
            return        
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.export_samplefilters(sel_chan_name=chan_name)

    def update_displays(self, context):
        if not self.rman_render.rman_interactive_running:
            return        
        self.rman_scene.bl_scene = context.scene    
        self.rman_scene._find_renderman_layer()
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):
            self.rman_scene.export_displays()         

    def texture_updated(self, nodeID):
        if not self.rman_render.rman_interactive_running:
            return        
        if nodeID == '':
            return
        tokens = nodeID.split('|')
        if len(tokens) < 2:
            return

        ob_name = tokens[0]
        node_name = tokens[1]
        node, ob = scene_utils.find_node_by_name(node_name, ob_name)
        if ob == None:
            return

        ob_type = type(ob)

        if isinstance(ob, bpy.types.Material):
            ob.node_tree.update_tag()
        elif isinstance(ob, bpy.types.NodeTree):
            ob.update_tag()
        elif ob_type == bpy.types.World:
            ob.update_tag()   
        else:
            # light, lightfilters, and cameras
            ob.update_tag(refresh={'DATA'})

    def flush_texture_cache(self, texture_list):
        if not self.rman_render.rman_interactive_running:
            return         
        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):  
            for tex in texture_list:
                self.rman_scene.sg_scene.InvalidateTexture(tex)   

    def update_enhance(self, context, x, y, zoom):
        if not self.rman_render.rman_interactive_running:
            return         
        rman_sg_camera = self.rman_scene.main_camera
        if rman_sg_camera.projection_shader.name.CStr() != 'PxrCamera':
            return

        with self.rman_scene.rman.SGManager.ScopedEdit(self.rman_scene.sg_scene):     
            res_x = int(self.rman_scene.viewport_render_res_mult * x)
            res_y = int(self.rman_scene.viewport_render_res_mult * y)
            projparams = rman_sg_camera.projection_shader.params         
            projparams.SetVector("enhance", [res_x, res_y, zoom])
            rman_sg_camera.sg_camera_node.SetProjection(rman_sg_camera.projection_shader)
