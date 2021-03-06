#!/usr/bin/env python

import sys
import os
import time
# import redis
import ConfigParser # this is version 2.x specific, on version 3.x it is called "configparser" and has a different API
import numpy as np
import pandas as pd

from copy import copy
from obspy.signal.detrend import polynomial

if hasattr(sys, 'frozen'):
    basis = sys.executable
elif sys.argv[0] != '':
    basis = sys.argv[0]
else:
    basis = './'
installed_folder = os.path.split(basis)[0]

# eegsynth/lib contains shared modules
sys.path.insert(0, os.path.join(installed_folder, '../../lib'))
import EEGsynth
import FieldTrip

config = ConfigParser.ConfigParser()
config.read(os.path.join(installed_folder, os.path.splitext(os.path.basename(__file__))[0] + '.ini'))

# this determines how much debugging information gets printed
debug = config.getint('general', 'debug')

try:
    ftc_host = config.get('input_fieldtrip', 'hostname')
    ftc_port = config.getint('input_fieldtrip', 'port')
    if debug > 0:
        print 'Trying to connect to buffer on %s:%i ...' % (ftc_host, ftc_port)
    ft_input = FieldTrip.Client()
    ft_input.connect(ftc_host, ftc_port)
    if debug > 0:
        print "Connected to input FieldTrip buffer"
except:
    print "Error: cannot connect to input FieldTrip buffer"
    exit()

try:
    ftc_host = config.get('output_fieldtrip', 'hostname')
    ftc_port = config.getint('output_fieldtrip', 'port')
    if debug > 0:
        print 'Trying to connect to buffer on %s:%i ...' % (ftc_host, ftc_port)
    ft_output = FieldTrip.Client()
    ft_output.connect(ftc_host, ftc_port)
    if debug > 0:
        print "Connected to output FieldTrip buffer"
except:
    print "Error: cannot connect to output FieldTrip buffer"
    exit()

hdr_input = None
while hdr_input is None:
    if debug > 0:
        print "Waiting for data to arrive..."
    hdr_input = ft_input.getHeader()
    time.sleep(0.2)

if debug > 1:
    print hdr_input
    print hdr_input.labels

# get the input and output options
input_number, input_channel = map(list, zip(*config.items('input_channel')))
output_number, output_channel = map(list, zip(*config.items('output_channel')))

# convert to integer and make the indices zero-offset
input_number = [int(number)-1 for number in input_number]
output_number = [int(number)-1 for number in output_number]

def sanitize(equation):
    equation = equation.replace('(', '( ')
    equation = equation.replace(')', ' )')
    equation = equation.replace('+', ' + ')
    equation = equation.replace('-', ' - ')
    equation = equation.replace('*', ' * ')
    equation = equation.replace('/', ' / ')
    equation = equation.replace('  ', ' ')
    return equation

# ensure that all input channels have a label
nInputs = hdr_input.nChannels
if len(hdr_input.labels) == 0:
    for i in range(nInputs):
        hdr_input.labels.append('{}'.format(i+1))
# update the labels with the ones specified in the ini file
for number, channel in zip(input_number, input_channel):
    if number < nInputs:
        hdr_input.labels[number] = channel
# update the input channel specification
input_number = range(nInputs)
input_channel = hdr_input.labels

# ensure that all output channels have a label
nOutputs = max(output_number)+1
tmp = ['{}'.format(i+1) for i in range(nOutputs)]
for number, channel in zip(output_number, output_channel):
    tmp[number] = channel
# update the output channel specification
output_number = range(nOutputs)
output_channel = tmp

if debug > 0:
    print '===== input channels ====='
    for number, channel in zip(input_number, input_channel):
        print number, '=', channel
    print '===== output channels ====='
    for number, channel in zip(output_number, output_channel):
        print number, '=', channel

previous = np.zeros((1, nOutputs))  # for exponential smoothing
sample_rate = config.getfloat('cogito', 'sample_rate')
window = config.getfloat('cogito', 'window')
f_min = config.getfloat('cogito', 'f_min')
f_max = config.getfloat('cogito', 'f_max')
scaling = config.getfloat('cogito', 'scaling')
window = int(round(window*hdr_input.fSample))

begsample = -1
while begsample < 0:
    # wait until there is enough data
    hdr_input = ft_input.getHeader()
    # jump to the end of the stream
    begsample = int(hdr_input.nSamples - window)
    endsample = int(hdr_input.nSamples - 1)

ft_output.putHeader(nOutputs, sample_rate, hdr_input.dataType, labels=output_channel)

# Reading EEG layout
layout = pd.read_csv("gtec_layout.csv", index_col=0)
definition = 10
val = layout.loc[:, ['x', 'y', 'z']].values
val = (val - val.min())/(val.max()-val.min())*definition
positions = np.round(val).astype(int)

print "STARTING COGITO STREAM"
while True:

    while endsample > hdr_input.nSamples-1:
        # wait until there is enough data
        time.sleep(config.getfloat('general', 'delay'))
        hdr_input = ft_input.getHeader()

    # if debug > 1:
    #     print "window", window
    #     print "nsample", hdr_input.nSamples
    #     print "endsample", endsample
    #     print "begsample", begsample

    dat_input = ft_input.getData([begsample, endsample])

    # t = np.arange(sample_rate)
    # f = 440
    # signal = np.sin(t*f/sample_rate)*256
    # signal = np.zeros([sample_rate, 1])

    tmp = []
    for ch in range(nInputs):
        channel = np.zeros(300)
        original = copy(dat_input[:, ch])
        polynomial(original, order=10, plot=False)

        # One
        channel.append(np.ones(1)*scaling/250.)

        # Spectrum
        fourier = np.fft.rfft(original, 250)[f_min:f_max]
        channel.append(fourier)
        # Positions
        mask = np.zeros(30)
        mask[(positions[ch][0]-1):positions[ch][0]] = scaling/250
        mask[(10+positions[ch][1]-1):(10+positions[ch][1])] = scaling/250
        mask[(20+positions[ch][2]-1):(20+positions[ch][2])] = scaling/250
        channel.append(mask)

        # Sum of all the parts
        convert = np.concatenate(channel)
        tmp.append(convert)
    signal = np.fft.irfft(np.concatenate(tmp), 44100)
    dat_output = np.atleast_2d(signal).T.astype(np.float32)

    # write the data to the output buffer
    ft_output.putData(dat_output)

    # increment the counters for the next loop
    begsample += window
    endsample += window
