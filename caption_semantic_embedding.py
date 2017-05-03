import numpy as np
import os
import h5py
import math

import MovieQA_benchmark as MovieQA
import CaptionDataUtil
import CaptionModelUtil
import ModelUtil
# import word2vec as w2v
import gensim
from gensim.models import KeyedVectors


os.environ["CUDA_VISIBLE_DEVICES"]="1"

import tensorflow as tf
from sklearn.decomposition import PCA
import cPickle as pickle
import time
import json

def build_model(input_video, input_captions, 
			v2i,w2v_model,visual_pca_mat=None,d_w2v=512,d_lproj=300,
			labels = None, lr=0.01):
	with tf.variable_scope('share_embedding_matrix') as scope:
		T_B, T_w2v, T_mask = CaptionModelUtil.setWord2VecModelConfiguration(v2i,w2v_model,d_w2v,d_lproj=d_lproj)
		# encode question


		embeded_caption_words, mask_c = CaptionModelUtil.getEmbeddingWithWord2Vec(input_captions, T_w2v, T_mask, T_B, d_lproj)

		embeded_video = CaptionModelUtil.getVisualEncoder(input_video, T_w2v, T_B, visual_pca_mat=visual_pca_mat, return_sequences=True)

		predict_score, predict_words = CaptionModelUtil.getCaptionEncoderDecoder(embeded_video, embeded_caption_words, T_w2v, T_mask, T_B, mask_c)



		loss = tf.nn.softmax_cross_entropy_with_logits(labels=labels, logits=predict_score)

		# train module
		loss = tf.reduce_mean(loss)
		# acc_value = tf.metrics.accuracy(y, embeded_question)
		optimizer = tf.train.RMSPropOptimizer(lr,decay=0.9, momentum=0.0, epsilon=1e-6)
		train = optimizer.minimize(loss)
		return train, loss, predict_words
		
def linear_project_pca_initialization(hf,  feature_shape, d_w2v=512, output_path=None):

	print('--utilize PCA to initialize the embedding matrix of feature to d_w2v')
	samples = []
	for vid in xrange(1200):
		feature = hf['vid'+str(vid+1)]
		feature = np.reshape(feature,feature_shape)
		axis = [0,2,3,1]
		feature = np.transpose(feature, tuple(axis))
		feature = np.reshape(feature,(-1,feature_shape[1]))
		feature = np.random.permutation(feature)
		samples.extend(feature[:200])
	print('samples:',len(samples))

	pca = PCA(n_components=d_w2v, whiten=True)
	pca_mat = pca.fit_transform(np.asarray(samples).T)  # 1024 x 300

	pickle.dump(pca_mat,open(output_path,'w'))
	print('pca_amt dump to file:',output_path)
	return pca_mat

def exe_train(sess, data, batch_size, v2i, hf, feature_shape, 
	train, loss, input_video, input_captions, y, capl=16):

	np.random.shuffle(data)

	total_data = len(data)
	num_batch = int(round(total_data*1.0/batch_size))

	total_loss = 0.0
	for batch_idx in xrange(num_batch):
		# if batch_idx < 100:
		batch_caption = data[batch_idx*batch_size:min((batch_idx+1)*batch_size,total_data)]

		data_v = CaptionDataUtil.getBatchVideoFeature(batch_caption,hf,feature_shape)
		data_c, data_y = CaptionDataUtil.getBatchTrainCaption(batch_caption, v2i, capl=capl)


		_, l = sess.run([train,loss],feed_dict={input_video:data_v, input_captions:data_c,  y:data_y})
		total_loss += l
		print('    batch_idx:%d/%d, loss:%.5f' %(batch_idx+1,num_batch,l))
	total_loss = total_loss/num_batch
	return total_loss

def exe_test(sess, data, batch_size, v2i, i2v, hf, feature_shape, 
	predict_words, input_video, input_captions, y, capl=16):
	
	caption_output = []
	total_data = len(data)
	num_batch = int(round(total_data*1.0/batch_size))+1

	for batch_idx in xrange(num_batch):
		batch_caption = data[batch_idx*batch_size:min((batch_idx+1)*batch_size,total_data)]
		
		data_v = CaptionDataUtil.getBatchVideoFeature(batch_caption,hf,feature_shape)
		data_c, data_y = CaptionDataUtil.getBatchTestCaption(batch_caption, v2i, capl=capl)

		[gw] = sess.run([predict_words],feed_dict={input_video:data_v, input_captions:data_c, y:data_y})

		generated_captions = CaptionDataUtil.convertCaptionI2V(batch_caption, gw, i2v)

		for idx, sen in enumerate(generated_captions):
			print('%s : %s' %(batch_caption[idx].keys()[0],sen))
			caption_output.append({'image_id':batch_caption[idx].keys()[0],'caption':sen})
	
	js = {}
	js['val_predictions'] = caption_output

	return js


def evaluate_mode_by_shell(res_path,js):
	with open(res_path, 'w') as f:
		json.dump(js, f)

	command ='/home/xyj/usr/local/code/caption_eval/call_python_caption_eval.sh '+ res_path
	os.system(command)


def main(hf,f_type,capl=16,d_w2v=512,d_lproj=300,
		feature_shape=None,lr=0.01,
		batch_size=8,total_epoch=100,
		file='./caption',pretrained_model=None,visual_pca_mat_file=None):
	'''
		capl: the length of caption
	'''

	# w2v_mqa_model_filename = './model/movie_plots_1364.d-300.mc1.w2v'
	# w2v_model = w2v.load(w2v_mqa_model_filename, kind='bin')
	print('loading GoogleNews vectors ... ...')
	w2v_googlenews_filename = '/home/xyj/usr/local/GoogleNewsW2V/new_w2v_caption.bin'
	w2v_model = KeyedVectors.load_word2vec_format(w2v_googlenews_filename, binary=True)

	# Create vocabulary
	v2i, train_data, val_data, test_data = CaptionDataUtil.create_vocabulary_word2vec(file, capl=capl, v2i={'': 0, 'UNK':1,'BOS':2, 'EOS':3})

	i2v = {i:v for v,i in v2i.items()}

	print('building model ...')
	if os.path.exists(visual_pca_mat_file):
		visual_pca_mat = pickle.load(open(visual_pca_mat_file,'r'))
	else:
		visual_pca_mat = linear_project_pca_initialization(hf, feature_shape, d_w2v=d_w2v, output_path=visual_pca_mat_file)

	print('visual_pca_mat.shape:',visual_pca_mat.shape)

	input_video = tf.placeholder(tf.float32, shape=(None,)+feature_shape,name='input_video')
	input_captions = tf.placeholder(tf.int32, shape=(None,capl), name='input_captions')

	y = tf.placeholder(tf.int32,shape=(None, capl, len(v2i)))

	train, loss, predict_words = build_model(input_video, input_captions, 
			v2i,w2v_model,visual_pca_mat=visual_pca_mat,d_w2v=d_w2v,d_lproj=d_lproj,
			labels=y, lr=lr)

	'''
		configure && runtime environment
	'''
	config = tf.ConfigProto()
	config.gpu_options.per_process_gpu_memory_fraction = 0.3
	# sess = tf.Session(config=tf.ConfigProto(log_device_placement=True))
	config.log_device_placement=False

	sess = tf.Session(config=config)

	init = tf.global_variables_initializer()
	sess.run(init)


	with sess.as_default():
		saver = tf.train.Saver(sharded=True,max_to_keep=total_epoch)
		if pretrained_model is not None:
			saver.restore(sess, pretrained_model)
			print('restore pre trained file:' + pretrained_model)

		for epoch in xrange(total_epoch):
			# # shuffle
			print('Epoch: %d/%d, Batch_size: %d' %(epoch+1,total_epoch,batch_size))
			# # train phase
			tic = time.time()
			total_loss = exe_train(sess, train_data, batch_size, v2i, hf, feature_shape, train, loss, input_video, input_captions, y, capl=capl)

			print('    --Train--, Loss: %.5f, .......Time:%.3f' %(total_loss,time.time()-tic))

			tic = time.time()
			js = exe_test(sess, test_data, batch_size, v2i, i2v, hf, feature_shape, 
										predict_words, input_video, input_captions, y, capl=capl)
			print('    --Val--, .......Time:%.3f' %(time.time()-tic))

			

			#save model
			export_path = '/home/xyj/usr/local/saved_model/caption/semantic'+'_'+f_type+'/'+'lr'+str(lr)+'_f'+str(feature_shape[0])
			if not os.path.exists(export_path+'/model'):
				os.makedirs(export_path+'/model')
				print('mkdir %s' %export_path+'/model')
			if not os.path.exists(export_path+'/res'):
				os.makedirs(export_path+'/res')
				print('mkdir %s' %export_path+'/res')

			# eval
			res_path = export_path+'/res/'+f_type+'_E'+str(epoch+1)+'.json'
			evaluate_mode_by_shell(res_path,js)


			save_path = saver.save(sess, export_path+'/model/'+'E'+str(epoch+1)+'_L'+str(total_loss)+'.ckpt')
			print("Model saved in file: %s" % save_path)
		

if __name__ == '__main__':


	lr = 0.0002


	
	video_feature_dims=1024
	timesteps_v=10 # sequences length for video
	hight = 7
	width = 7
	feature_shape = (timesteps_v,video_feature_dims,hight,width)

	f_type = 'GoogleNet'
	feature_path = '/home/xyj/usr/local/data/YouTube/feature/in5b-'+str(timesteps_v)+'fpv.h5'
	visual_pca_mat_file = '/home/xyj/usr/local/code/VideoQA/model/pca_mat/caption_google_pca_mat512.pkl'

	'''
	---------------------------------
	'''
	hf = h5py.File(feature_path,'r')

	pretrained_model = None
	
	main(hf,f_type,capl=13,d_w2v=512,d_lproj=512,
		feature_shape=feature_shape,lr=lr,
		batch_size=64,total_epoch=100,
		file='./caption',pretrained_model=None,visual_pca_mat_file=visual_pca_mat_file)
	

	
	
	
	


	