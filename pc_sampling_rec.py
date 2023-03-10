import tensorflow as tf
from numpy import *
import numpy as np
import os
import getdata
import tf_util
import copy
import random
from tf_ops.emd import tf_auctionmatch
from tf_ops.CD import tf_nndistance
from tf_ops.sampling import tf_sampling
from ae_sam import mlp_architecture_ala_iclr_18,movenet
from tf_ops.grouping import tf_grouping
from provider import shuffle_data,shuffle_points,rotate_point_cloud,jitter_point_cloud
from samplenet import SoftProjection,get_project_loss 
import argparse

os.environ["CUDA_VISIBLE_DEVICES"]="0"

def chamfer_big(pcd1, pcd2):
    dist1, idx1, dist2, idx2 = tf_nndistance.nn_distance(pcd1, pcd2)
    dist1 = tf.reduce_mean(tf.sqrt(dist1))
    dist2 = tf.reduce_mean(tf.sqrt(dist2))
    #dist=tf.reduce_mean(tf.maximum(tf.sqrt(dist1),tf.sqrt(dist2)))
    dist=(dist1 + dist2)/2
    return dist
def sampling(npoint,xyz,use_type='f'):
    if use_type=='f':
        idx=tf_sampling.farthest_point_sample(npoint, xyz)
        new_xyz=tf_sampling.gather_point(xyz,idx)
    elif use_type=='r':
        bnum=tf.shape(xyz)[0]
        ptnum=xyz.get_shape()[1].value
        ptids=arange(ptnum)
        random.shuffle(ptids)
        ptid=tf.tile(tf.constant(ptids[:npoint],shape=[1,npoint,1],dtype=tf.int32),[bnum,1,1])
        bid=tf.tile(tf.reshape(tf.range(start=0,limit=bnum,dtype=tf.int32),[-1,1,1]),[1,npoint,1])
        idx=tf.concat([bid,ptid],axis=-1)
        new_xyz=tf.gather_nd(xyz,idx)
    return new_xyz
#def train(ptnum=2048,bneck_size=128,margin=0.001,diswei=1.0):
def train(args):
    dnum=3
    pointcloud_pl=tf.placeholder(tf.float32,[None,args.ptnum,dnum],name='pointcloud_pl')
    knum=tf.placeholder(tf.float32,name='pointcloud_pl')
    global_step=tf.Variable(0,trainable=False)
    with tf.variable_scope('sam'):
        inpts,movelen=movenet(pointcloud_pl,knum=knum,mlp1=[128,256,256],mlp2=[128,128],startcen=None,infer=False)#Driving sampled points
    encoder, decoder, enc_args, dec_args = mlp_architecture_ala_iclr_18(args.ptnum,args.bneck,dnum,mode='fc')
    with tf.variable_scope('ge'):
        word=encoder(proinpts,n_filters=enc_args['n_filters'],filter_sizes=enc_args['filter_sizes'],strides=enc_args['strides'],b_norm=enc_args['b_norm'],b_norm_decay=1.0,verbose=enc_args['verbose'])
        out=decoder(word,layer_sizes=dec_args['layer_sizes'],b_norm=dec_args['b_norm'],b_norm_decay=1.0,b_norm_finish=dec_args['b_norm_finish'],b_norm_decay_finish=1.0,verbose=dec_args['verbose'])
    loss1=chamfer_big(pointcloud_pl,out)
    loss_e=loss1+args.movewei*tf.reduce_mean(movelen)
    trainvars=tf.GraphKeys.GLOBAL_VARIABLES

    var1=tf.get_collection(trainvars,scope='sam')
    regularizer=tf.contrib.layers.l2_regularizer(0.0001)
    gezhengze=tf.reduce_sum([regularizer(v) for v in var1])
    loss_e=loss_e+args.reg*gezhengze

    trainstep=[]
    trainstep.append(tf.train.AdamOptimizer(learning_rate=args.lr).minimize(loss_e, global_step=global_step,var_list=var1))
    loss=[loss_e]

    config=tf.ConfigProto(allow_soft_placement=True,log_device_placement=False)
    config.gpu_options.allow_growth=True
    with tf.Session(config=config) as sess:
        gevar=tf.get_collection(trainvars,scope='ge') 
        saver = tf.train.Saver(max_to_keep=10)
        sess.run(tf.global_variables_initializer())
        if os.path.exists(os.path.join(args.prepath,'checkpoint')):
            print('here load')
            tf.train.Saver(var_list=gevar).restore(sess, tf.train.latest_checkpoint(args.prepath))
        else:
            print('There is not pretrained network!')
            assert False

        datalist=[]
        kklist=[np.power(2,v) for v in list(range(args.reso_start,args.reso_end))]
        kknum=len(kklist)
        plist=np.ones(kknum)/kknum

        trainfiles=getdata.getfile(os.path.join(args.filepath,'train_files.txt'))
        filenum=len(trainfiles)

        #read the data to memory
        for j in range(filenum):
            datalist.append(getdata.load_h5(os.path.join(args.filepath, trainfiles[j])))

        for i in range(args.itertime): 
            for j in range(filenum):
                traindata = copy.deepcopy(datalist[j]) 
                ids=list(range(len(traindata)))
                random.shuffle(ids)
                traindata=traindata[ids,:,:]
                traindata=shuffle_points(traindata[:,:args.ptnum])
                
                allnum=int(len(traindata)/args.batch_size)*args.batch_size
                batch_num=int(allnum/args.batch_size)
                
                for batch in range(batch_num):
                    start_idx = (batch * args.batch_size) % allnum
                    end_idx=(batch*args.batch_size)%allnum+args.batch_size
                    batch_point=traindata[start_idx:end_idx]
                    idx=np.random.choice(len(kklist),1,p=list(plist))[0]
                    kk=kklist[idx]
                    feed_dict = {pointcloud_pl: batch_point,knum:kk}
                    resi = sess.run([trainstep[0],loss], feed_dict=feed_dict)
                    losse=resi[1]

                    if (batch+1) % args.seestep == 0:
                        print('sample num', kk)
                        print('epoch: %d '%i,'file: %d '%j,'batch: %d' %batch)
                        print('loss: ',resi[1])
                                                
            if (i+1)%args.savestep==0:
                save_path = tf.train.Saver(var_list=var1).save(sess, os.path.join(args.savepath,'model'),global_step=i)
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ptnum', type=int, default=2048, help='The number of points')
    parser.add_argument('--bneck', type=int, default=128, help='The size of bottleneck layer in AE')
    parser.add_argument('--movewei', type=float, default=0.001, help='The weights of moving distances')
    parser.add_argument('--reso_start', type=int, default=6, help='Controlling the lowest resolution')
    parser.add_argument('--reso_end', type=int, default=12, help='Controlling the highest resolution')
    parser.add_argument('--batch_size', type=int, default=16, help='Batch size')
    parser.add_argument('--savestep', type=int, default=100, help='The interval to save checkpoint')
    parser.add_argument('--seestep', type=int, default=16, help='The batch interval to see training errors')
    parser.add_argument('--itertime', type=int, default=1000, help='The number of epochs for iteration')
    parser.add_argument('--filepath', type=str, default='./data', help='The path of h5 training data')
    parser.add_argument('--savepath', type=str, default='./modelvv_sam/', help='The path of saved checkpoint')
    parser.add_argument('--prepath', type=str, default='./ae_cd/', help='The checkpoint path of the pretrained task network')
    parser.add_argument('--reg', type=float, default=0.001, help='The regularization parameter')
    parser.add_argument('--lr', type=float, default=0.0001, help='The learning rate')
    args=parser.parse_args()
    train(args)
