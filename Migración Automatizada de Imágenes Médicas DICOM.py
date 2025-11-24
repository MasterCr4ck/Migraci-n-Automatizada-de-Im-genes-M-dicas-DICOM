import datetime
import logging
import time
import threading
import argparse
import sys


from pydicom.dataset import Dataset
from pynetdicom import (
    AE, evt, StoragePresentationContexts, AllStoragePresentationContexts, debug_logger,build_role
)
from pydicom.uid import ImplicitVRLittleEndian,ExplicitVRLittleEndian

from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelGet,
    PatientStudyOnlyQueryRetrieveInformationModelGet,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    CTImageStorage,
    MRImageStorage,
    UltrasoundImageStorage,
    SecondaryCaptureImageStorage
)
from pynetdicom.presentation import build_context

# Configuracion

#configuracion del server origen
ORTHANC_AET = 'AET server'
ORTHANC_IP = 'ip'
ORTHANC_PORT = 11113

# configuracion del server destino (orthanc)
DCM4CHEE_AET = 'DCM4CHEE'
DCM4CHEE_IP = 'ip orthanc'
DCM4CHEE_PORT = 11112

# configuracion del servidor pacs temporal (para solicitudes get y reenvio del C-GET)
LOCAL_AET = 'RADIANT'
LOCAL_PORT = 11114

# Configuracion mejorada de logging (log de las transacciones realizadas)
logging.basicConfig(
    filename='log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DicomRetrievalService:
    def __init__(self):
        self.scp_ae = None
        self.scp_thread = None
        self.images_received = 0
        self.images_forwarded = 0
        
    def get_critical_storage_contexts(self):
        """Retorna solo los contextos mas criticos para evitar el limite"""
        # CONTEXTOS CRiTICOS EN ORDEN DE PRIORIDAD
        critical_contexts = [
            # 1. Radiografia computada - EL MaS IMPORTANTE
            ComputedRadiographyImageStorage,
            # 2. Rayos X digitales
            DigitalXRayImageStorageForPresentation,
            DigitalXRayImageStorageForProcessing,
            # 3. Modalidades principales
            CTImageStorage,
            MRImageStorage,
            UltrasoundImageStorage,
            SecondaryCaptureImageStorage,
            ImplicitVRLittleEndian,
            ExplicitVRLittleEndian
        ]
        
        # Agregar contextos adicionales mas comunes hasta un limite muy conservador
        # Usar solo 50 contextos para C-GET (muy conservador)
        MAX_CONTEXTS = 50
        current_count = len(critical_contexts)
        
        # Obtener contextos adicionales mas comunes
        additional_contexts = []
        common_sop_classes = [
            '1.2.840.10008.5.1.4.1.2.2.3',
            '1.2.840.10008.5.1.4.1.1.88.22',
            '1.2.840.10008.5.1.4.1.1.9.2.1',
            '1.2.840.10008.5.1.4.1.1.77.1.4',
            '1.2.840.10008.5.1.4.1.1.1.2',
            '1.2.840.10008.5.1.4.1.1.6.2',
            '1.2.840.10008.5.1.4.1.1.130',
            '1.2.840.10008.5.1.4.1.1.9.3.1',
            '1.2.840.10008.5.1.4.1.1.128',
            '1.2.840.10008.5.1.4.1.1.4.2',
            '1.2.840.10008.5.1.4.1.1.88.67',
            '1.2.840.10008.5.1.4.1.1.1.3',
            '1.2.840.10008.5.1.4.1.1.77.1.4.1',
            '1.2.840.10008.5.1.4.1.1.3.1',
            '1.2.840.10008.5.1.4.1.1.1.2.1',
            '1.2.840.10008.5.1.4.1.1.88.59',
            '1.2.840.10008.5.1.4.1.1.1.1',
            '1.2.840.10008.5.1.4.1.1.1.1.1',
            '1.2.840.10008.5.1.4.1.1.77.1.6',
            '1.2.840.10008.5.1.4.1.1.6.1',
            '1.2.840.10008.5.1.4.1.1.2',
            '1.2.840.10008.5.1.4.1.1.104.3',
            '1.2.840.10008.5.1.4.1.1.2.1',
            '1.2.840.10008.5.1.4.1.1.12.1.1',
            '1.2.840.10008.5.1.4.1.1.11.1',
            '1.2.840.10008.5.1.4.1.1.104.1',
            '1.2.840.10008.5.1.4.1.1.7',
            '1.2.840.10008.5.1.4.1.1.9.1.2',
            '1.2.840.10008.5.1.4.1.1.1.3.1',
            '1.2.840.10008.5.1.4.1.1.88.11',
            '1.2.840.10008.5.1.4.1.1.9.1.1',
            '1.2.840.10008.5.1.4.1.1.12.2.1',
            '1.2.840.10008.5.1.4.1.1.20',
            '1.2.840.10008.5.1.4.1.1.1',
            '1.2.840.10008.5.1.4.1.1.9.1.3',
            '1.2.840.10008.5.1.4.1.1.13.1.1',
            '1.2.840.10008.5.1.4.1.1.12.2',
            '1.2.840.10008.5.1.4.1.1.4',
            '1.2.840.10008.5.1.4.1.1.104.2',
            '1.2.840.10008.5.1.4.1.1.4.1',
            '1.2.840.10008.5.1.4.1.1.12.1'
        ]
        
        for ctx in AllStoragePresentationContexts:
            if current_count >= MAX_CONTEXTS:
                break
            #logger.info(f" recorriendo contexto: {ctx}")
            # Agregar contextos comunes que no esten ya incluidos
            if (ctx.abstract_syntax not in critical_contexts and str(ctx.abstract_syntax) in common_sop_classes):
                    additional_contexts.append(ctx.abstract_syntax)
                    current_count += 1
        
        # Combinar contextos criticos con adicionales
        all_contexts = critical_contexts + additional_contexts
        
        logger.info(f"=======> Usando {len(all_contexts)} contextos criticos (limite conservador)")
        #logger.info(f"=======> ComputedRadiographyImageStorage en posicion: {all_contexts.index(ComputedRadiographyImageStorage)}")
        
        return all_contexts
    
    def test_dcm4chee_context_support(self):
        """Prueba que contextos acepta DCM4CHEE especificamente"""
        logger.info("===============================> Probando soporte de contextos en DCM4CHEE...")
        
        # Probar solo ComputedRadiographyImageStorage
        test_ae = AE(ae_title=LOCAL_AET)
        
        # 1. Contexto para query/retrieve
        test_ae.add_supported_context(StudyRootQueryRetrieveInformationModelGet,scp_role=True, scu_role=True)
        test_ae.add_supported_context(ComputedRadiographyImageStorage,ImplicitVRLittleEndian,scp_role=True, scu_role=True)
        
        # ComputedRadiographyImageStorage context con sintaxis de transferencia especial:
        test_ae.add_requested_context(ComputedRadiographyImageStorage,ImplicitVRLittleEndian)
        test_ae.add_requested_context(ComputedRadiographyImageStorage)

        for contex in ComputedRadiographyImageStorage:
            test_ae.add_supported_context(contex,ImplicitVRLittleEndian,True,True)
        
        
        try:
            assoc = test_ae.associate(DCM4CHEE_IP, DCM4CHEE_PORT, ae_title=DCM4CHEE_AET)
            
            if assoc.is_established:
                logger.info("!!!!!!!!!!!!!!!!!!!!-------------------     Conexion de prueba establecida     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                
                # Verificar si CR esta aceptado
                cr_accepted = False
                for ctx in assoc.accepted_contexts:
                    logger.info(f"=======> Contexto aceptado: ID={ctx.context_id} SOP={ctx.abstract_syntax}")
                    
                    if str(ctx.abstract_syntax) == str(ComputedRadiographyImageStorage):
                        logger.info(f"=======> ComputedRadiographyImageStorage ACEPTADO: {ctx.abstract_syntax}")
                        cr_accepted = True
                        break
                
                if not cr_accepted:
                    logger.error("=======> ComputedRadiographyImageStorage NO ACEPTADO")
                    logger.info("=======> Contextos aceptados en la prueba:")
                    for ctx in assoc.accepted_contexts:
                        logger.info(f"  - {ctx.abstract_syntax}")
                
                assoc.release()
                return cr_accepted
            else:
                logger.error("!!!!!!!!!!!!!!!!!!!!-------------------     No se pudo establecer conexion de prueba     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                return False
                
        except Exception as e:
            logger.error(f"!!!!!!!!!!!!!!!!!!!!-------------------     Error en test_dcm4chee_context_support: {e}     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
            return False
        
    def test_Orthanc_context_support(self):
        """Prueba que contextos acepta ORTHANC especificamente"""
        logger.info("===============================> Probando soporte de contextos en ORTHANC...")
        
        # Probar solo ComputedRadiographyImageStorage
        test_ae = AE(ae_title=LOCAL_AET)
        
        # 1. Contexto para query/retrieve
        test_ae.add_supported_context(StudyRootQueryRetrieveInformationModelGet,scp_role=True, scu_role=True)
        test_ae.add_supported_context(ComputedRadiographyImageStorage,ImplicitVRLittleEndian,scp_role=True, scu_role=True)
        
        # ComputedRadiographyImageStorage context con sintaxis de transferencia especial:
        test_ae.add_requested_context(ComputedRadiographyImageStorage,ImplicitVRLittleEndian)
        test_ae.add_requested_context(ComputedRadiographyImageStorage)

        for contex in ComputedRadiographyImageStorage:
            test_ae.add_supported_context(contex,ImplicitVRLittleEndian,True,True)
        
        
        try:
            assoc = test_ae.associate(ORTHANC_IP, ORTHANC_PORT, ae_title=ORTHANC_AET)
            
            if assoc.is_established:
                logger.info("!!!!!!!!!!!!!!!!!!!!-------------------     Conexion de prueba establecida     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                
                # Verificar si CR esta aceptado
                cr_accepted = False
                for ctx in assoc.accepted_contexts:
                    logger.info(f"=======> Contexto aceptado: ID={ctx.context_id} SOP={ctx.abstract_syntax}")
                    
                    if str(ctx.abstract_syntax) == str(ComputedRadiographyImageStorage):
                        logger.info(f"=======> ComputedRadiographyImageStorage ACEPTADO: {ctx.abstract_syntax}")
                        cr_accepted = True
                        break
                
                if not cr_accepted:
                    logger.error("=======> ComputedRadiographyImageStorage NO ACEPTADO")
                    logger.info("=======> Contextos aceptados en la prueba:")
                    for ctx in assoc.accepted_contexts:
                        logger.info(f"  - {ctx.abstract_syntax}")
                
                assoc.release()
                return cr_accepted
            else:
                logger.error("!!!!!!!!!!!!!!!!!!!!-------------------     No se pudo establecer conexion de prueba     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                return False
                
        except Exception as e:
            logger.error(f"!!!!!!!!!!!!!!!!!!!!-------------------     Error en test_Orthanc_context_support: {e}     -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
            return False
    
    def create_optimized_cget_ae(self):
        """Crea un AE optimizado para C-GET con contextos limitados y handlers configurados"""
        ae_get = AE(ae_title=LOCAL_AET)
        
        # 1. Contexto para query/retrieve
        ae_get.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        ae_get.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
        ae_get.add_requested_context(PatientStudyOnlyQueryRetrieveInformationModelGet)
        
        # Tambien (por si haces algun C-STORE como cliente):
        ae_get.add_requested_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        ae_get.add_supported_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        # IMPORTANTE: Agrega tambien la query como SCU (para hacer C-GET)
        ae_get.add_requested_context(StudyRootQueryRetrieveInformationModelGet, ImplicitVRLittleEndian)
        ae_get.add_requested_context(ComputedRadiographyImageStorage)
        
        
        ae_get.add_supported_context(StudyRootQueryRetrieveInformationModelGet,scp_role=True, scu_role=True)
         # Agregar el contexto para recibir imagenes como SCP
        ae_get.add_supported_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian, scp_role=True)
        
        for contex in ComputedRadiographyImageStorage:
            ae_get.add_supported_context(contex,ImplicitVRLittleEndian,True,True)
            
        
        # 2. Obtener contextos criticos
        critical_contexts = self.get_critical_storage_contexts()
        
        # 3. Agregar contextos uno por uno con manejo de errores
        # IMPORTANTE: Para C-GET, necesitamos configurar estos contextos como SCP
        # porque el servidor DCM4CHEE nos enviara las imagenes
        added_contexts = []
        for ctx in critical_contexts:
            try:
                # Agregar como contexto de almacenamiento SCP (para recibir imagenes)
                ae_get.add_supported_context(ctx, scp_role=True, scu_role=True)
                ae_get.add_requested_context(ctx)
                added_contexts.append(ctx)
                #logger.info(f"=======> Contexto SCP agregado: {ctx}")
            except Exception as e:
                logger.warning(f"=======> No se pudo agregar contexto {ctx}: {e}")
                break
        
        logger.info(f"=======> Total contextos requested en AE: {len(ae_get.requested_contexts)}")
        logger.info(f"=======> Total contextos supported en AE: {len(ae_get.supported_contexts)}")
        logger.info(f"=======> Contextos de almacenamiento agregados: {len(added_contexts)}")
        
        # Verificar que CR esta incluido
        cr_included = ComputedRadiographyImageStorage in added_contexts
        logger.info(f"=======> ComputedRadiographyImageStorage incluido: {cr_included}")
        
        
        # CRITICO: Agregar el handler para C-STORE al AE que hara el C-GET
        handlers = [(evt.EVT_C_STORE, self.handle_store)]
        
        # Asignar los handlers al AE
        for event_type, handler in handlers:
            ae_get.on_c_store = handler
        
        logger.info("=======> Handler C-STORE configurado en el AE para C-GET")
        
        return ae_get
    
    def handle_store(self, event):
        """Maneja la recepcion de imagenes DICOM y las reenvia a Orthanc (para SCP independiente)"""
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            
            self.images_received += 1
            
            # Mostrar informacion del SOP Class recibido
            sop_class = ds.get('SOPClassUID', 'Unknown')
            logger.info(f"===============================> Imagen #{self.images_received} recibida - SOP Class: {sop_class}")
            
            # Reenvia a Orthanc
            forward_ae = AE()
            forward_ae.add_requested_context(event.context.abstract_syntax)
            
            assoc = forward_ae.associate(
                ORTHANC_IP, 
                ORTHANC_PORT, 
                ae_title=ORTHANC_AET
            )
            
            if assoc.is_established:
                status = assoc.send_c_store(ds)
                assoc.release()
                
                if status.Status == 0x0000:
                    self.images_forwarded += 1
                    logger.info(f"=======> Imagen #{self.images_received} enviada a Orthanc exitosamente")
                else:
                    logger.error(f"=======> Error enviando imagen a Orthanc: 0x{status.Status:04x}")
            else:
                logger.error("=======> No se pudo conectar con Orthanc para enviar imagen")
                
            return 0x0000
            
        except Exception as e:
            logger.error(f"=======> Error en handle_store: {e}")
            return 0xA700  # Out of Resources
    
    def start_scp(self):
        """Inicia el SCP configurado especificamente para dcm4chee 1.4"""
        self.scp_ae = AE(ae_title=LOCAL_AET)
        
        self.scp_ae.add_supported_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        self.scp_ae.add_requested_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        
        # 2. Obtener contextos criticos
        critical_contexts = self.get_critical_storage_contexts()
        
        # Agregar contextos usando el metodo add_supported_context()
        for sop_class in critical_contexts:
            logger.info(f"contextos usando el metodo add_supported_context para contexto: {sop_class}")
            self.scp_ae.add_supported_context(sop_class, scp_role=True, scu_role=True)
            self.scp_ae.add_requested_context(sop_class)
            #self.scp_ae.add_supported_context(sop_class)
        
        self.scp_ae.add_supported_context(CTImageStorage, ExplicitVRLittleEndian)
        
        handlers = [(evt.EVT_C_STORE, self.handle_store)]
        
        try:
            self.scp_thread = self.scp_ae.start_server(
                ('0.0.0.0', LOCAL_PORT), 
                block=False, 
                evt_handlers=handlers
            )
            logger.info(f"=======> SCP iniciado para dcm4chee 1.4 en puerto {LOCAL_PORT}")
            logger.info(f"=======> Contextos soportados: {len(self.scp_ae.supported_contexts)}")
            
            return True
        except Exception as e:
            logger.error(f"=======> Error iniciando SCP: {e}")
            return False
    
    def stop_scp(self):
        """Detiene el SCP"""
        if self.scp_thread:
            self.scp_thread.shutdown()
            logger.info("=======> SCP detenido")
    
    def find_studies(self, study_date):
        """Busca estudios por fecha"""
        find_ds = Dataset()
        find_ds.QueryRetrieveLevel = 'STUDY'
        find_ds.StudyDate = study_date
        find_ds.StudyInstanceUID = ''
        find_ds.PatientName = ''
        find_ds.PatientID = ''
        find_ds.AccessionNumber = ''
        find_ds.StudyDescription = ''
        
        ae = AE()
        # Agregamos contexto para busqueda
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        
        #arreglo para almacenar estudios encontrados
        studies = []
        
        try:
            assoc = ae.associate(DCM4CHEE_IP, DCM4CHEE_PORT, ae_title=DCM4CHEE_AET)
            
            if assoc.is_established:
                logger.info(f"!!!!!!!!!!!!!!!!!!!!-------------------  Buscando estudios para fecha: {study_date} en DCM4CHEE -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                responses = assoc.send_c_find(find_ds, StudyRootQueryRetrieveInformationModelFind)
                
                for status, identifier in responses:
                    if status and status.Status in (0xFF00, 0xFF01):
                        study_uid = identifier.StudyInstanceUID
                        patient_name = getattr(identifier, 'PatientName', 'N/A')
                        patient_id = getattr(identifier, 'PatientID', 'N/A')
                        study_desc = getattr(identifier, 'StudyDescription', 'N/A')
                        
                        studies.append({
                            'uid': study_uid,
                            'patient_name': patient_name,
                            'patient_id': patient_id,
                            'description': study_desc
                        })
                        
                        logger.info(f"=======> Estudio encontrado: {patient_name} ({patient_id}) - {study_desc}")
                
                assoc.release()
                logger.info(f"=======> Se encontraron {len(studies)} estudios")
                
            else:
                logger.error("=======> No se pudo establecer conexión con DCM4CHEE para FIND")
                
        except Exception as e:
            logger.error(f"=======> Error en find_studies: {e}")
            
        return studies
    
    def find_studies_Orthanc(self, study_date):
        """Busca estudios por fecha"""
        find_ds = Dataset()
        find_ds.QueryRetrieveLevel = 'STUDY'
        find_ds.StudyDate = study_date
        find_ds.StudyInstanceUID = ''
        find_ds.PatientName = ''
        find_ds.PatientID = ''
        find_ds.AccessionNumber = ''
        find_ds.StudyDescription = ''
        
        ae = AE(ae_title=LOCAL_AET)
        # Agregamos contexto para busqueda
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        
        #arreglo para almacenar estudios encontrados
        studies = []
        
        try:
            assoc = ae.associate(ORTHANC_IP, ORTHANC_PORT, ae_title=ORTHANC_AET)
            
            if assoc.is_established:
                logger.info(f"!!!!!!!!!!!!!!!!!!!!-------------------  Buscando estudios para fecha: {study_date} en ORTHANC -------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")
                responses = assoc.send_c_find(find_ds, StudyRootQueryRetrieveInformationModelFind)
                
                for status, identifier in responses:
                    if status and status.Status in (0xFF00, 0xFF01):
                        study_uid = identifier.StudyInstanceUID
                        patient_name = getattr(identifier, 'PatientName', 'N/A')
                        patient_id = getattr(identifier, 'PatientID', 'N/A')
                        study_desc = getattr(identifier, 'StudyDescription', 'N/A')
                        
                        studies.append({
                            'uid': study_uid,
                            'patient_name': patient_name,
                            'patient_id': patient_id,
                            'description': study_desc
                        })
                        
                        logger.info(f"=======> Estudio encontrado: {patient_name} ({patient_id}) - {study_desc}")
                
                assoc.release()
                logger.info(f"=======> Se encontraron {len(studies)} estudios")
                
            else:
                logger.error("=======> No se pudo establecer conexión con DCM4CHEE para FIND")
                
        except Exception as e:
            logger.error(f"=======> Error en find_studies: {e}")
            
        return studies

    def retrieve_study_optimized(self, study_uid):
        """Recupera un estudio con configuración optimizada"""
        logger.info(f"=======> Iniciando C-GET optimizado para estudio: {study_uid}")

        # Crear AE optimizado CON HANDLERS CONFIGURADOS
        ae_get = self.create_optimized_cget_ae()
        
        ae_get.add_supported_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        ae_get.add_requested_context(ComputedRadiographyImageStorage, ImplicitVRLittleEndian)
        
        ext_neg = []
        for cx in StoragePresentationContexts:
            ext_neg.append(build_role(cx.abstract_syntax, scp_role=True))
        
        # Crear dataset para C-GET
        get_ds = Dataset()
        get_ds.QueryRetrieveLevel = 'STUDY'
        get_ds.StudyInstanceUID = study_uid

        # Preparar handlers
        handlers = [(evt.EVT_C_STORE, self.handle_store)]
        


        try:
            # Establecer asociación
            logger.info("=======> Estableciendo asociación optimizada...")
            assoc_get = ae_get.associate(
                DCM4CHEE_IP,
                DCM4CHEE_PORT,
                ae_title=DCM4CHEE_AET,
                ext_neg= ext_neg,
                evt_handlers= handlers)

            if assoc_get.is_established:
                logger.info("=======> Asociación establecida exitosamente")
                logger.info(f"=======> Contextos aceptados: {len(assoc_get.accepted_contexts)}")

                # Enviar C-GET
                logger.info("===============================> Enviando C-GET...")
                responses_get = assoc_get.send_c_get(
                    get_ds, 
                    query_model=StudyRootQueryRetrieveInformationModelGet
                )

                success = False
                for status_get, _ in responses_get:
                    if status_get:
                        if status_get.Status == 0x0000:
                            logger.info("=======> C-GET completado exitosamente")
                            success = True
                        elif status_get.Status in (0xFF00, 0xFF01):
                            logger.info(f"=======> C-GET en progreso: 0x{status_get.Status:04x}")
                        else:
                            logger.warning(f"=======> Estado C-GET: 0x{status_get.Status:04x}")

                assoc_get.release()
                return success

            else:
                logger.error("=======> No se pudo establecer asociación")
                return False

        except Exception as e:
            logger.error(f"=======> Error en retrieve_study_optimized: {e}")
            return False

    
    def run_retrieval(self, study_date=None):
        """Ejecuta el proceso completo de recuperación con optimizaciones"""
        if not study_date:
            study_date = datetime.date.today().strftime('%Y%m%d') #formato para la fecha actual
        
        logger.info(f"=======> Iniciando proceso optimizado para fecha: {study_date}........\n\n\n ")
        
        # 1. Probar soporte de contextos
        if not self.test_dcm4chee_context_support():
            logger.error("=======> DCM4CHEE no soporta ComputedRadiographyImageStorage")
            logger.info("=======> Continuando con otros contextos disponibles...\n\n\n\n")
            
        #probar conexión a server orthanc
        #Orthanc_Service = DicomRetrievalService()
        #Orthanc = Orthanc_Service.test_Orthanc_context_support
        
        if not self.test_Orthanc_context_support():
            logger.error("=======> ORTHANC no soporta ComputedRadiographyImageStorage")
            logger.info("=======> Continuando con otros contextos disponibles...\n\n\n\n")
        
        
        try:
            # 4. Buscar estudios servidor hcm3chee
            studies = self.find_studies(study_date)
            
            # Buscar estudios servidor Orthanc para solo procesar los validos
            studies_orthanc = self.find_studies_Orthanc(study_date)
            
            if not studies:
                logger.info("=======> No se encontraron estudios para la fecha especificada dentro del servidor dcm4chee")
                return True
            
            # exluimos estudios ya existentes en orthanc
            if len(studies_orthanc)>0:
                #buscamos el uid en la lista de studies de dcm4chee y eliminamos el registro
                studies = [study for study in studies 
                           if study not in studies_orthanc]

            if not studies:
                logger.info("=======> No se encontraron estudios no existentes en el servidor Orthanc para la fecha especificada")
                return True
            
            # 5. Incianmos servidor csp para procesar los estudios
            logger.info(f"!!!!!!!!!!!!!!!!!!!!------------------- Iniciando SCP temporal en puerto {LOCAL_PORT} para recibir imágenes  -------------------!!!!!!!!!!!!!!!!!!!!\n\n")
            if not self.start_scp():
                return False
            
            
            # 6. Procesar los estudios (debug)
            logger.info(f"\n\n\n!!!!!!!!!!!!!!!!!!!!------------------- Procesando {len(studies)} estudios -------------------!!!!!!!!!!!!!!!!!!!!")
            for i, study in enumerate(studies, start=1):
                logger.info(f"=======> Procesando estudio {i}/{len(studies)} - UID: {study['uid']}")
                initial_count = self.images_received

                success = self.retrieve_study_optimized(study['uid'])

                logger.info("=======> Esperando procesamiento de imágenes...\n\n")
                time.sleep(10)  # Puedes ajustar este valor si sabes que tarda más/menos

                images_for_this_study = self.images_received - initial_count
                logger.info(f"=======> Estudio {i}: {images_for_this_study} imágenes recibidas")

                if images_for_this_study > 0:
                    logger.info("=======> ¡Estudio procesado exitosamente!")
                else:
                    logger.warning("=======> No se recibieron imágenes para este estudio")

            logger.info(f"=======> Total general: {self.images_received} recibidas, {self.images_forwarded} enviadas a Orthanc")
            return success
            
        except Exception as e:
            logger.error(f"=======> Error en run_retrieval: {e}")
            return False
        
        finally:
            # 9. Limpiar t detener server scp
            logger.info("!!!!!!!!!!!!!!!!!!!!-------------------  Limpiando... -------------------!!!!!!!!!!!!!!!!!!!!")
            time.sleep(3)
            self.stop_scp()

def main():
    """Función principal"""
    #debug_logger() # log completo para transferencia 
    # almanecamos fecha y hora de ejecución en variable Fch_Ejecucion
    
    Fch_ejecucion = datetime.date.today().strftime('%Y-%m-%d')
    Hora_ejecucion = datetime.datetime.now().strftime('%H:%M:%S')
    
    logger.info(f"\n\n\n")
    logger.info(f"!!!!!!!!!!!!!!!!!!!!----------------------------------------------------------------------------!!!!!!!!!!!!!!!!!!!!")
    logger.info(f"!!!!!!!!!!!!!!!!!!!!-------------------Fecha de ejecucion: {Fch_ejecucion} {Hora_ejecucion} -------------------!!!!!!!!!!!!!!!!!!!!")
    logger.info(f"!!!!!!!!!!!!!!!!!!!!----------------------------------------------------------------------------!!!!!!!!!!!!!!!!!!!!\n\n\n")

    service = DicomRetrievalService()
    
    try:
        success = service.run_retrieval()
        
        if success:
            logger.info("!!!!!!!!!!!!!!!!!!!!-------------------Proceso completado exitosamente-------------------!!!!!!!!!!!!!!!!!!!!")
        else:
            logger.error("!!!!!!!!!!!!!!!!!!!!-------------------El proceso falló-------------------!!!!!!!!!!!!!!!!!!!!")
            
    except KeyboardInterrupt:
        logger.warning("!!!!!!!!!!!!!!!!!!!!------------------- Proceso interrumpido por el usuario-------------------!!!!!!!!!!!!!!!!!!!!")
        service.stop_scp()
    except Exception as e:
        logger.error(f"!!!!!!!!!!!!!!!!!!!!------------------- Error inesperado: {e} -------------------!!!!!!!!!!!!!!!!!!!!")
        service.stop_scp()

if __name__ == "__main__":
    main()